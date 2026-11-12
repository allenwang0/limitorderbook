cmake_minimum_required(VERSION 3.25)
project(limitorderbook LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

option(LOB_WERROR "Treat compiler warnings as errors" OFF)
option(LOB_SANITIZE "Build with AddressSanitizer and UndefinedBehaviorSanitizer" OFF)

add_library(lob_warnings INTERFACE)
target_compile_options(lob_warnings INTERFACE -Wall -Wextra -Wpedantic $<$<BOOL:${LOB_WERROR}>:-Werror>)

if(LOB_SANITIZE)
  add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all)
  add_link_options(-fsanitize=address,undefined)
endif()

include(FetchContent)
set(BENCHMARK_ENABLE_TESTING OFF CACHE BOOL "" FORCE)
set(BENCHMARK_ENABLE_GTEST_TESTS OFF CACHE BOOL "" FORCE)
set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)
FetchContent_Declare(googletest
  GIT_REPOSITORY https://github.com/google/googletest.git
  GIT_TAG v1.15.2
  GIT_SHALLOW TRUE)
FetchContent_Declare(benchmark
  GIT_REPOSITORY https://github.com/google/benchmark.git
  GIT_TAG v1.9.1
  GIT_SHALLOW TRUE)
FetchContent_MakeAvailable(googletest benchmark)

add_library(lob STATIC src/MapBook.cpp src/FlatBook.cpp)
target_include_directories(lob PUBLIC include)
target_link_libraries(lob PRIVATE lob_warnings)

execute_process(COMMAND git rev-parse HEAD
  WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
  OUTPUT_VARIABLE LOB_GIT_SHA OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
if(APPLE)
  execute_process(COMMAND sysctl -n machdep.cpu.brand_string
    OUTPUT_VARIABLE LOB_CPU OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
elseif(EXISTS /proc/cpuinfo)
  file(STRINGS /proc/cpuinfo LOB_CPU_LINE REGEX "^model name" LIMIT_COUNT 1)
  string(REGEX REPLACE "^model name[ \t]*:[ \t]*" "" LOB_CPU "${LOB_CPU_LINE}")
endif()
string(TOUPPER "${CMAKE_BUILD_TYPE}" LOB_BUILD_TYPE_UPPER)
set(LOB_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${CMAKE_CXX_FLAGS_${LOB_BUILD_TYPE_UPPER}} -std=c++17")
string(STRIP "${LOB_CXX_FLAGS}" LOB_CXX_FLAGS)

add_executable(lob_bench bench/lob_bench.cpp)
target_link_libraries(lob_bench PRIVATE lob lob_warnings benchmark::benchmark)
target_compile_definitions(lob_bench PRIVATE
  LOB_GIT_SHA="${LOB_GIT_SHA}" LOB_CPU="${LOB_CPU}" LOB_CXX_FLAGS="${LOB_CXX_FLAGS}")


enable_testing()
include(GoogleTest)
add_executable(lob_tests tests/book_test.cpp)
target_link_libraries(lob_tests PRIVATE lob lob_warnings GTest::gtest_main)
gtest_discover_tests(lob_tests)

add_executable(lob_differential_test tests/differential_test.cpp)
target_link_libraries(lob_differential_test PRIVATE lob lob_warnings GTest::gtest_main)
gtest_discover_tests(lob_differential_test PROPERTIES TIMEOUT 1800)
