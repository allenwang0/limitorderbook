CXX = g++
CXXFLAGS = -std=c++17 -O3 -Wall -Wextra

Files

TARGET = lob_sim
SOURCES = main.cpp OrderBook.cpp
HEADERS = OrderBook.h Order.h

Rules

all: $(TARGET)

$(TARGET): $(SOURCES) $(HEADERS)
$(CXX) $(CXXFLAGS) -o $(TARGET) $(SOURCES)

clean:
rm -f $(TARGET)

docker-build:
docker build -t lob-sim .

docker-run:
docker run --rm lob-sim