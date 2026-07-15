with open('lelab/record.py', 'r') as f:
    lines = f.readlines()

new_lines = []
in_while = False
for i, line in enumerate(lines):
    if 'while saved_episodes < cfg.dataset.num_episodes:' in line:
        in_while = True
        new_lines.append(line)
        continue
        
    if in_while:
        # Stop at the end of the while loop, which is "# Recording completed"
        if line.strip() == '# Recording completed':
            in_while = False
            new_lines.append(line)
            continue
            
        # Indent everything inside the while loop by 4 spaces
        # Wait, the `with VideoEncodingManager` added 4 spaces.
        # But wait, the while loop is ALREADY indented.
        # If I want to indent the INSIDE of the while loop, I have to indent the while loop itself too?
        pass

