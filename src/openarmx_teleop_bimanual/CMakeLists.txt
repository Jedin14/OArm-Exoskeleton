cmake_minimum_required(VERSION 3.8)
project(openarmx_teleop_bimanual)

# Set C++ standard
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# Find dependencies
find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(std_msgs REQUIRED)
find_package(openarmx_can REQUIRED)
find_package(urdf REQUIRED)
find_package(urdfdom_headers REQUIRED)
find_package(orocos_kdl REQUIRED)
find_package(kdl_parser REQUIRED)
find_package(urdfdom REQUIRED)
find_package(Eigen3 REQUIRED)

include_directories(
  include
  
)

# Dynamics library
add_library(openarmx_teleop_bimanual_dynamics STATIC
  src/dynamics.cpp
)

target_include_directories(openarmx_teleop_bimanual_dynamics PUBLIC
  include

)

ament_target_dependencies(openarmx_teleop_bimanual_dynamics
  urdf
  orocos_kdl
  kdl_parser
  urdfdom
  urdfdom_headers
)

target_link_libraries(openarmx_teleop_bimanual_dynamics
  openarmx_can::openarmx_can

)

# Add executables

# teleop_bimanual_node (without gravity compensation)
add_executable(teleop_bimanual_node
  src/teleop_bimanual_node.cpp
)

target_link_libraries(teleop_bimanual_node
  openarmx_can::openarmx_can
)

ament_target_dependencies(teleop_bimanual_node
  rclcpp
  std_msgs
)

# teleop_bimanual_with_gravitycomp_single (with gravity compensation)
add_executable(teleop_bimanual_with_gravitycomp_single
  src/teleop_bimanual_with_gravitycomp_single.cpp
)

target_link_libraries(teleop_bimanual_with_gravitycomp_single
  openarmx_teleop_bimanual_dynamics
)

ament_target_dependencies(teleop_bimanual_with_gravitycomp_single
  rclcpp
  std_msgs
)

# Install executables
install(TARGETS
  teleop_bimanual_node
  teleop_bimanual_with_gravitycomp_single
  DESTINATION lib/${PROJECT_NAME}
)

# Install launch files
install(DIRECTORY
  launch
  DESTINATION share/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  set(ament_cmake_copyright_FOUND TRUE)
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
