// OpenArmX Research and Education License
//
// Copyright (c) 2026 Chengdu Changshu Robot Co., Ltd.
// https://www.openarmx.com
//
// This software is licensed for non-commercial research, academic,
// and educational use only.
// Commercial use is strictly prohibited without prior written permission.
//
// Redistribution for permitted non-commercial purposes must retain
// this notice.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.

#include "dynamics.hpp"
#include <map>

Dynamics::Dynamics(std::string urdf_path, std::string start_link, std::string end_link)
{
        this->urdf_path = urdf_path;
        this->start_link = start_link;
        this->end_link = end_link;
}

Dynamics::~Dynamics(){}


bool Dynamics::Init()
{
    std::ifstream file(urdf_path);
    if (!file.is_open()) {
        fprintf(stderr, "Failed to open URDF file: %s\n", urdf_path.c_str());
        return false;
    }

    std::stringstream buffer;
    buffer << file.rdbuf();
    file.close();

    urdf_model_interface = urdf::parseURDF(buffer.str());
    if (!urdf_model_interface) {
        fprintf(stderr, "Failed to parse URDF: %s\n", urdf_path.c_str());
        return false;
    }

    if (!kdl_parser::treeFromUrdfModel(*urdf_model_interface, kdl_tree)) {
        fprintf(stderr, "Failed to extract KDL tree: %s\n", urdf_path.c_str());
        return false;
    }

    if (!kdl_tree.getChain(start_link, end_link, kdl_chain)) {
        fprintf(stderr, "Failed to get KDL chain\n");
        return false;
    }

    std::cout << "[GetGravity] kdl_chain.getNrOfJoints() = " << kdl_chain.getNrOfJoints() << std::endl;

    coriolis_forces.resize(kdl_chain.getNrOfJoints());
    gravity_forces.resize(kdl_chain.getNrOfJoints());
    inertia_matrix.resize(kdl_chain.getNrOfJoints());
    

    coriolis_forces.data.setZero();
    gravity_forces.data.setZero();
    inertia_matrix.data.setZero();

    solver = std::make_unique<KDL::ChainDynParam>(
        kdl_chain, gravity_vec_);

    return true;
}

void Dynamics::SetGravityVector(double gx, double gy, double gz)
{
        gravity_vec_ = KDL::Vector(gx, gy, gz);
        // Recreate solver with new gravity vector
        solver = std::make_unique<KDL::ChainDynParam>(kdl_chain, gravity_vec_);
}

void Dynamics::GetGravity(const double *motor_position, double *gravity)
{

        const auto njoints = kdl_chain.getNrOfJoints();
    
        KDL::JntArray q_(kdl_chain.getNrOfJoints());

        for(size_t i = 0; i < kdl_chain.getNrOfJoints(); i++) {
                q_(i) = motor_position[i];
        }

        solver->JntToGravity(q_, gravity_forces);
        for(size_t i = 0; i < kdl_chain.getNrOfJoints(); i++) {
                gravity[i] = gravity_forces(i);
        }
}

void Dynamics::GetColiori(const double *motor_position, const double *motor_velocity, double *colioli) {
        KDL::JntArray q_(kdl_chain.getNrOfJoints());
        KDL::JntArray q_dot(kdl_chain.getNrOfJoints());

        for(size_t i = 0; i < kdl_chain.getNrOfJoints(); i++) {
                q_(i) = motor_position[i];
                q_dot(i) = motor_velocity[i];
        }

        solver->JntToCoriolis(q_, q_dot, coriolis_forces);

        for(size_t i = 0; i < kdl_chain.getNrOfJoints(); i++) {
                colioli[i] = coriolis_forces(i);
        }
}

void Dynamics::GetMassMatrixDiagonal(const double *motor_position, double *inertia_diag)
{
        KDL::JntArray q_(kdl_chain.getNrOfJoints());
        KDL::JntSpaceInertiaMatrix inertia_matrix(kdl_chain.getNrOfJoints());
        for(size_t i = 0; i < kdl_chain.getNrOfJoints(); i++) {
                q_(i) = motor_position[i];
        }

        solver->JntToMass(q_, inertia_matrix);

        for(size_t i = 0; i < kdl_chain.getNrOfJoints(); i++) {
                inertia_diag[i] = inertia_matrix(i, i);
        }

}

void Dynamics::GetJacobian(const double *motor_position, Eigen::MatrixXd &jacobian)
{
        KDL::JntArray q_(kdl_chain.getNrOfJoints());
        for (size_t i = 0; i < kdl_chain.getNrOfJoints(); ++i) {
                q_(i) = motor_position[i];
        }

        KDL::Jacobian kdl_jac(kdl_chain.getNrOfJoints());
        KDL::ChainJntToJacSolver jac_solver(kdl_chain);
        jac_solver.JntToJac(q_, kdl_jac);

        jacobian = Eigen::MatrixXd(6, kdl_chain.getNrOfJoints());
        for (size_t i = 0; i < 6; ++i) {
                for (size_t j = 0; j < kdl_chain.getNrOfJoints(); ++j) {
                        jacobian(i, j) = kdl_jac(i, j);
                }
        }

}

void Dynamics::GetNullSpace(const double* motor_position, Eigen::MatrixXd& nullspace) {
        const size_t dof = kdl_chain.getNrOfJoints();

        bool use_stable_svd = false;

        Eigen::MatrixXd J;
        GetJacobian(motor_position, J);

        Eigen::MatrixXd J_pinv;

        if (use_stable_svd) {
                Eigen::JacobiSVD<Eigen::MatrixXd> svd(J, Eigen::ComputeThinU | Eigen::ComputeThinV);
                double tol = 1e-6 * std::max(J.cols(), J.rows()) * svd.singularValues().array().abs().maxCoeff();
                Eigen::VectorXd singularValuesInv = svd.singularValues();
                for (int i = 0; i < singularValuesInv.size(); ++i) {
                        singularValuesInv(i) = (singularValuesInv(i) > tol) ? 1.0 / singularValuesInv(i) : 0.0;
                }
                J_pinv = svd.matrixV() * singularValuesInv.asDiagonal() * svd.matrixU().transpose();
        } else {
                J_pinv = J.transpose() * (J * J.transpose()).inverse();
        }

        Eigen::MatrixXd I = Eigen::MatrixXd::Identity(dof, dof);
        nullspace = I - J_pinv * J;

        //        std::cout << "[INFO] Null space projector computed.\n";
}


void Dynamics::GetNullSpaceTauSpace(const double* motor_position, Eigen::MatrixXd& nullspace_T)
{
        const size_t dof = kdl_chain.getNrOfJoints();
        bool use_stable_svd = false;

        Eigen::MatrixXd J;
        GetJacobian(motor_position, J);

        Eigen::MatrixXd J_pinv;

        if (use_stable_svd) {
                Eigen::JacobiSVD<Eigen::MatrixXd> svd(J, Eigen::ComputeThinU | Eigen::ComputeThinV);
                double tol = 1e-6 * std::max(J.cols(), J.rows()) * svd.singularValues().array().abs().maxCoeff();
                Eigen::VectorXd singularValuesInv = svd.singularValues();
                for (int i = 0; i < singularValuesInv.size(); ++i) {
                        singularValuesInv(i) = (singularValuesInv(i) > tol) ? 1.0 / singularValuesInv(i) : 0.0;
                }
                J_pinv = svd.matrixV() * singularValuesInv.asDiagonal() * svd.matrixU().transpose();
        } else {
                J_pinv = J.transpose() * (J * J.transpose()).inverse();
        }

        Eigen::MatrixXd I = Eigen::MatrixXd::Identity(dof, dof);
        Eigen::MatrixXd N = I - J_pinv * J;

        nullspace_T = N.transpose();
}

void Dynamics::GetEECordinate(const double *motor_position, Eigen::Matrix3d &R, Eigen::Vector3d &p)
{
        KDL::JntArray q_(kdl_chain.getNrOfJoints());
        for (size_t i = 0; i < kdl_chain.getNrOfJoints(); ++i) {
                q_(i) = motor_position[i];
        }

        KDL::ChainFkSolverPos_recursive fk_solver(kdl_chain);
        KDL::Frame kdl_frame;

        if (fk_solver.JntToCart(q_, kdl_frame) < 0) {
                //  std::cerr << "[KDL] FK failed in GetEECordinate!" << std::endl;
                return;
        }

        for (int i = 0; i < 3; ++i)
                for (int j = 0; j < 3; ++j)
                        R(i, j) = kdl_frame.M(i, j);

        p << kdl_frame.p[0], kdl_frame.p[1], kdl_frame.p[2];
}

void Dynamics::GetPreEECordinate(const double *motor_position, Eigen::Matrix3d &R, Eigen::Vector3d &p)
{
        KDL::JntArray q_(kdl_chain.getNrOfJoints());
        for (size_t i = 0; i < kdl_chain.getNrOfJoints(); ++i) {
                q_(i) = motor_position[i];
        }

        KDL::ChainFkSolverPos_recursive fk_solver(kdl_chain);
        KDL::Frame kdl_frame;

        if (fk_solver.JntToCart(q_, kdl_frame, kdl_chain.getNrOfSegments() - 1) < 0) {
                //        std::cerr << "[KDL] FK failed in GetPreEECordinate!" << std::endl;
                return;
        }

        for (int i = 0; i < 3; ++i)
                for (int j = 0; j < 3; ++j)
                        R(i, j) = kdl_frame.M(i, j);

        p << kdl_frame.p[0], kdl_frame.p[1], kdl_frame.p[2];



}




void Dynamics::PrintModelSummary() const
{
        std::cout << "[Dynamics] URDF: " << urdf_path << "\n";
        std::cout << "[Dynamics] Chain: '" << start_link << "' -> '" << end_link << "'" << "\n";
        std::cout << "[Dynamics] DOF (KDL joints): " << kdl_chain.getNrOfJoints() << "\n";

        // Map from link name to URDF link to check inertials
        std::map<std::string, urdf::LinkSharedPtr> link_map = urdf_model_interface->links_;

        size_t seg_idx = 0;
        size_t inertial_ok = 0;
        for (const auto &seg : kdl_chain.segments) {
                const auto &j = seg.getJoint();
                const auto &child = seg.getName();
                const std::string child_link = child;

                double mass = -1.0;
                Eigen::Vector3d com(0,0,0);
                bool has_inertial = false;

                auto it = link_map.find(child_link);
                if (it != link_map.end() && it->second && it->second->inertial) {
                        has_inertial = true;
                        mass = it->second->inertial->mass;
                        const auto &o = it->second->inertial->origin;
                        com = Eigen::Vector3d(o.position.x, o.position.y, o.position.z);
                        inertial_ok++;
                }

                std::cout << "  [seg " << seg_idx++ << "] link='" << child_link
                          << "' joint='" << j.getName() << "' type=" << j.getType()
                          << " inertial=" << (has_inertial ? "yes" : "NO")
                          << (has_inertial ? (", m=" + std::to_string(mass) + ", com=[" +
                                std::to_string(com.x()) + ", " + std::to_string(com.y()) + ", " + std::to_string(com.z()) + "]") : "")
                          << "\n";
        }

        std::cout << "[Dynamics] Inertials present on " << inertial_ok << "/" << kdl_chain.segments.size() << " segments." << std::endl;
}
