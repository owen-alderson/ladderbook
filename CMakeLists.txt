cmake_minimum_required(VERSION 3.20)
project(ladderbook LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(ladderbook_core INTERFACE)
target_include_directories(ladderbook_core INTERFACE cpp/include)

option(LADDERBOOK_BUILD_TESTS "Build the Catch2 test suite" OFF)

if(SKBUILD)
  set(PYBIND11_FINDPYTHON ON)
  find_package(pybind11 CONFIG REQUIRED)
  pybind11_add_module(_core cpp/src/bindings.cpp)
  target_link_libraries(_core PRIVATE ladderbook_core)
  install(TARGETS _core DESTINATION ladderbook)
endif()

if(LADDERBOOK_BUILD_TESTS)
  include(FetchContent)
  FetchContent_Declare(Catch2
    GIT_REPOSITORY https://github.com/catchorg/Catch2.git
    GIT_TAG v3.7.1)
  FetchContent_MakeAvailable(Catch2)
  file(GLOB TEST_SOURCES CONFIGURE_DEPENDS cpp/tests/*.cpp)
  add_executable(core_tests ${TEST_SOURCES})
  target_link_libraries(core_tests PRIVATE ladderbook_core Catch2::Catch2WithMain)
  target_compile_options(core_tests PRIVATE -Wall -Wextra -Werror)
  enable_testing()
  add_test(NAME core_tests COMMAND core_tests)
endif()
