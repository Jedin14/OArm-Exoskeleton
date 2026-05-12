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
// This logic was borrowed (then namespaced) from the examples on the gcc wiki:
//     https://gcc.gnu.org/wiki/Visibility

#if defined _WIN32 || defined __CYGWIN__
#ifdef __GNUC__
#define TEMPLATES__ROS2_CONTROL__VISIBILITY_EXPORT __attribute__((dllexport))
#define TEMPLATES__ROS2_CONTROL__VISIBILITY_IMPORT __attribute__((dllimport))
#else
#define TEMPLATES__ROS2_CONTROL__VISIBILITY_EXPORT __declspec(dllexport)
#define TEMPLATES__ROS2_CONTROL__VISIBILITY_IMPORT __declspec(dllimport)
#endif
#ifdef TEMPLATES__ROS2_CONTROL__VISIBILITY_BUILDING_DLL
#define TEMPLATES__ROS2_CONTROL__VISIBILITY_PUBLIC \
  TEMPLATES__ROS2_CONTROL__VISIBILITY_EXPORT
#else
#define TEMPLATES__ROS2_CONTROL__VISIBILITY_PUBLIC \
  TEMPLATES__ROS2_CONTROL__VISIBILITY_IMPORT
#endif
#define TEMPLATES__ROS2_CONTROL__VISIBILITY_PUBLIC_TYPE \
  TEMPLATES__ROS2_CONTROL__VISIBILITY_PUBLIC
#define TEMPLATES__ROS2_CONTROL__VISIBILITY_LOCAL
#else
#define TEMPLATES__ROS2_CONTROL__VISIBILITY_EXPORT \
  __attribute__((visibility("default")))
#define TEMPLATES__ROS2_CONTROL__VISIBILITY_IMPORT
#if __GNUC__ >= 4
#define TEMPLATES__ROS2_CONTROL__VISIBILITY_PUBLIC \
  __attribute__((visibility("default")))
#define TEMPLATES__ROS2_CONTROL__VISIBILITY_LOCAL \
  __attribute__((visibility("hidden")))
#else
#define TEMPLATES__ROS2_CONTROL__VISIBILITY_PUBLIC
#define TEMPLATES__ROS2_CONTROL__VISIBILITY_LOCAL
#endif
#define TEMPLATES__ROS2_CONTROL__VISIBILITY_PUBLIC_TYPE
#endif
