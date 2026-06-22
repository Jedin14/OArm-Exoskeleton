import os
import yaml
import time
from threading import Thread, Lock
from flask import Flask, jsonify, request, send_from_directory, render_template

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import String

app = Flask(__name__)

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'data', 'config.yaml')
TRAJ_FILE = os.path.join(BASE_DIR, 'data', 'trajectories.yaml')
IMAGES_DIR = os.path.abspath(os.path.join(BASE_DIR, '..', 'object_images'))

# --- ROS 2 Node ---
class BeastArmsNode(Node):
    def __init__(self):
        super().__init__('beast_arms_ui_node')
        self.teleop_mode_pub = self.create_publisher(String, '/teleop_mode', 10)
        self.left_replay_pub = self.create_publisher(JointState, '/left_arm/replay_command', 10)
        self.right_replay_pub = self.create_publisher(JointState, '/right_arm/replay_command', 10)
        self.joint_state_sub = self.create_subscription(JointState, '/commanded_joint_states', self.joint_state_callback, 10)
        
        self.recording = False
        self.recorded_path = []
        self.lock = Lock()

    def set_teleop_mode(self, mode):
        msg = String()
        msg.data = mode
        self.teleop_mode_pub.publish(msg)

    def joint_state_callback(self, msg):
        with self.lock:
            if self.recording:
                self.recorded_path.append({
                    'time': float(time.time()),
                    'name': [str(n) for n in msg.name],
                    'position': [float(p) for p in msg.position]
                })

    def start_recording(self):
        with self.lock:
            self.recorded_path = []
            self.recording = True
        self.set_teleop_mode('teleop')

    def stop_recording(self):
        self.set_teleop_mode('idle')
        time.sleep(0.1) # Wait a bit for bridge to stop
        with self.lock:
            self.recording = False
            return list(self.recorded_path)

    def replay_trajectory(self, trajectory):
        if not trajectory:
            return
        
        self.set_teleop_mode('replay')
        time.sleep(0.1)
        
        start_time = time.time()
        traj_start_time = trajectory[0]['time']
        
        left_names = [f'openarm_left_joint{i}' for i in range(1,8)]
        right_names = [f'openarm_right_joint{i}' for i in range(1,8)]
        left_gripper = 'openarm_left_finger_joint1'
        right_gripper = 'openarm_right_finger_joint1'
        
        for point in trajectory:
            target_time = start_time + (point['time'] - traj_start_time)
            sleep_dur = target_time - time.time()
            if sleep_dur > 0:
                time.sleep(sleep_dur)
            
            left_pos = [0.0]*8
            right_pos = [0.0]*8
            name_idx = {name: idx for idx, name in enumerate(point['name'])}
            
            for i in range(7):
                if left_names[i] in name_idx:
                    left_pos[i] = point['position'][name_idx[left_names[i]]]
                if right_names[i] in name_idx:
                    right_pos[i] = point['position'][name_idx[right_names[i]]]
                    
            if left_gripper in name_idx:
                left_pos[7] = point['position'][name_idx[left_gripper]]
            if right_gripper in name_idx:
                right_pos[7] = point['position'][name_idx[right_gripper]]
                
            left_msg = JointState()
            left_msg.position = left_pos
            self.left_replay_pub.publish(left_msg)
            
            right_msg = JointState()
            right_msg.position = right_pos
            self.right_replay_pub.publish(right_msg)
            
        self.set_teleop_mode('idle')

ros_node = None
def ros2_spin_thread():
    rclpy.init()
    global ros_node
    ros_node = BeastArmsNode()
    rclpy.spin(ros_node)
    ros_node.destroy_node()
    rclpy.shutdown()

# Start ROS 2 thread
Thread(target=ros2_spin_thread, daemon=True).start()

# --- Helpers ---
def load_yaml(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r') as f:
            data = yaml.safe_load(f)
            return data if data is not None else default
    except Exception as e:
        print(f"Error loading YAML {path}: {e}")
        return default

def save_yaml(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + '.tmp'
    try:
        with open(tmp_path, 'w') as f:
            yaml.dump(data, f)
        os.replace(tmp_path, path)
    except Exception as e:
        print(f"Error saving YAML {path}: {e}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

# --- Flask Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/images/<path:filename>')
def serve_image(filename):
    return send_from_directory(IMAGES_DIR, filename)

@app.route('/api/config', methods=['GET'])
def get_config():
    config = load_yaml(CONFIG_FILE, {'objects': [], 'points': []})
    trajectories = load_yaml(TRAJ_FILE, {'combinations': {}})
    config['trajectories'] = list(trajectories.get('combinations', {}).keys())
    return jsonify(config)

@app.route('/api/config/objects', methods=['POST'])
def add_object():
    data = request.json
    config = load_yaml(CONFIG_FILE, {'objects': [], 'points': []})
    config['objects'].append({
        'id': f"obj_{data['name'].replace(' ', '_').lower()}",
        'name': data['name'],
        'image': data['image']
    })
    save_yaml(CONFIG_FILE, config)
    return jsonify({"status": "ok"})

@app.route('/api/config/points', methods=['POST'])
def add_point():
    data = request.json
    config = load_yaml(CONFIG_FILE, {'objects': [], 'points': []})
    config['points'].append({
        'id': f"pt_{data['name'].replace(' ', '_').lower()}",
        'name': data['name']
    })
    save_yaml(CONFIG_FILE, config)
    return jsonify({"status": "ok"})

@app.route('/api/teleop/toggle', methods=['POST'])
def toggle_teleop():
    data = request.json
    state = data.get('state')
    if ros_node:
        if state == 'on':
            ros_node.set_teleop_mode('teleop')
        elif state == 'off':
            ros_node.set_teleop_mode('idle')
        return jsonify({"status": "ok", "state": state})
    return jsonify({"error": "ROS 2 not initialized"}), 500

@app.route('/api/record/start', methods=['POST'])
def record_start():
    if ros_node:
        ros_node.start_recording()
        return jsonify({"status": "recording started"})
    return jsonify({"error": "ROS 2 not initialized"}), 500

@app.route('/api/record/cancel', methods=['POST'])
def record_cancel():
    if ros_node:
        ros_node.stop_recording() # we just throw away the path
        return jsonify({"status": "recording cancelled"})
    return jsonify({"error": "ROS 2 not initialized"}), 500

@app.route('/api/record/stop', methods=['POST'])
def record_stop():
    data = request.json
    combo_id = data.get('combo_id')
    if ros_node and combo_id:
        path = ros_node.stop_recording()
        trajectories = load_yaml(TRAJ_FILE, {'combinations': {}})
        if 'combinations' not in trajectories or not trajectories['combinations']:
            trajectories['combinations'] = {}
        trajectories['combinations'][combo_id] = path
        save_yaml(TRAJ_FILE, trajectories)
        return jsonify({"status": "recording saved", "points": len(path)})
    return jsonify({"error": "Invalid request"}), 400

@app.route('/api/replay', methods=['POST'])
def replay():
    data = request.json
    combo_id = data.get('combo_id')
    if ros_node and combo_id:
        trajectories = load_yaml(TRAJ_FILE, {'combinations': {}})
        path = trajectories.get('combinations', {}).get(combo_id)
        if path:
            Thread(target=ros_node.replay_trajectory, args=(path,), daemon=True).start()
            return jsonify({"status": "replaying"})
        return jsonify({"error": "Trajectory not found"}), 404
    return jsonify({"error": "Invalid request"}), 400

@app.route('/api/trajectories/<combo_id>', methods=['DELETE'])
def delete_trajectory(combo_id):
    trajectories = load_yaml(TRAJ_FILE, {'combinations': {}})
    if 'combinations' in trajectories and combo_id in trajectories['combinations']:
        del trajectories['combinations'][combo_id]
        save_yaml(TRAJ_FILE, trajectories)
        return jsonify({"status": "deleted"})
    return jsonify({"error": "Not found"}), 404

if __name__ == '__main__':
    # Add a short delay to ensure ROS 2 node is up
    time.sleep(1)
    app.run(host='0.0.0.0', port=5000, debug=False)
