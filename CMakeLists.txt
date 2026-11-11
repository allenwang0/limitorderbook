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

add_library(lob STATIC src/MapBook.cpp)
target_include_directories(lob PUBLIC include)
target_link_libraries(lob PRIVATE lob_warnings)

add_executable(lob_bench bench/lob_bench.cpp)
target_link_libraries(lob_bench PRIVATE lob lob_warnings benchmark::benchmark)

enable_testing()
include(GoogleTest)
add_executable(lob_tests tests/book_test.cpp)
target_link_libraries(lob_tests PRIVATE lob lob_warnings GTest::gtest_main)
gtest_discover_tests(lob_tests)
