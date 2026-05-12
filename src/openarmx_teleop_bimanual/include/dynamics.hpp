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

#pragma once
#include <unistd.h>
#include <string.h>
#include <kdl/chain.hpp>
#include <kdl/chaindynparam.hpp>
#include <kdl_parser/kdl_parser.hpp>
// #include <kdl_parser/kdl_parser.hpp>
#include <kdl/chainfksolverpos_recursive.hpp>
#include <kdl/chainjnttojacsolver.hpp>
#include <urdf_parser/urdf_parser.h>
#include <Eigen/Dense>
#include <vector>
#include <iostream>
#include <fstream>
#include <sstream>
/*
 * Compute gravity and inertia compensation using Orocos
 * Kinematics and Dynamics Library (KDL).
 */
class Dynamics
{
        private:
                std::shared_ptr<urdf::ModelInterface> urdf_model_interface;

                std::string urdf_path;
                std::string start_link;
                std::string end_link;

                KDL::JntSpaceInertiaMatrix inertia_matrix;
                KDL::JntArray q;
                KDL::JntArray q_d;
                KDL::JntArray coriolis_forces;
                KDL::JntArray gravity_forces;

                KDL::JntArray biasangle;

                KDL::Tree kdl_tree;
                KDL::Chain kdl_chain;
                std::unique_ptr<KDL::ChainDynParam> solver;
                KDL::Vector gravity_vec_{0.0, 0.0, -9.81};

        public:
                Dynamics(std::string urdf_path, std::string start_link, std::string end_link);
                ~Dynamics();
                
                bool Init();
                void SetGravityVector(double gx, double gy, double gz);
                void GetGravity(const double *motor_position, double *gravity);
                void GetColiori(const double *motor_position, const double *motor_velocity, double *colioli);
                void GetMassMatrixDiagonal(const double *motor_position, double *inertia_diag);

                void GetJacobian(const double *motor_position, Eigen::MatrixXd &jacobian);
 
                void GetNullSpace(const double *motor_positon, Eigen::MatrixXd &nullspace);

                void GetNullSpaceTauSpace(const double* motor_position, Eigen::MatrixXd& nullspace_T);

                void GetEECordinate(const double *motor_position, Eigen::Matrix3d &R, Eigen::Vector3d &p);
                
                void GetPreEECordinate(const double *motor_position, Eigen::Matrix3d &R, Eigen::Vector3d &p);

                // Debug helpers
                size_t NumJoints() const { return kdl_chain.getNrOfJoints(); }
                std::string StartLink() const { return start_link; }
                std::string EndLink() const { return end_link; }
                // Print basic info about the chain and inertials used by KDL
                void PrintModelSummary() const;
};

