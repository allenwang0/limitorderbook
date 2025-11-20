#include <iostream>
#include <vector>
#include <chrono>
#include <random>
#include <iomanip>
#include "OrderBook.h"

// Configuration
const int NUM_ORDERS = 1000000;
const Price START_PRICE = 10000; // $100.00

// Generate a random set of orders to simulate market activity
std::vector<Order> generateOrders(int count) {
    std::vector<Order> orders;
    orders.reserve(count);

    std::mt19937 rng(42); // Fixed seed for reproducibility
    std::uniform_int_distribution<int> sideDist(0, 1);
    std::uniform_int_distribution<int> priceDist(-50, 50); // Price drift
    std::uniform_int_distribution<int> qtyDist(1, 100);

    Price currentPrice = START_PRICE;

    for (OrderId i = 1; i <= count; ++i) {
        Side side = (sideDist(rng) == 0) ? Side::Buy : Side::Sell;
        
        // Random walk price generation
        currentPrice += priceDist(rng);
        if (currentPrice < 100) currentPrice = 100; // Floor

        // If buying, we might bid slightly lower; if selling, slightly higher
        // to simulate spread, but occasionally cross to trigger trades.
        Price orderPrice = currentPrice + (side == Side::Buy ? -5 : 5);
        
        orders.push_back({i, side, orderPrice, (Quantity)qtyDist(rng)});
    }
    return orders;
}

int main() {
    std::cout << "Generating " << NUM_ORDERS << " random orders..." << std::endl;
    auto orders = generateOrders(NUM_ORDERS);
    
    OrderBook book;

    std::cout << "Starting benchmark..." << std::endl;

    // Start Timer
    auto start = std::chrono::high_resolution_clock::now();

    // The Hot Loop
    for (const auto& order : orders) {
        book.addOrder(order);
    }

    // Stop Timer
    auto end = std::chrono::high_resolution_clock::now();
    
    auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end - start);
    double seconds = duration.count() / 1000.0;
    double ops = NUM_ORDERS / seconds;

    std::cout << "--------------------------------" << std::endl;
    std::cout << "Processing Complete." << std::endl;
    std::cout << "Orders Processed: " << NUM_ORDERS << std::endl;
    std::cout << "Time Taken:       " << duration.count() << " ms" << std::endl;
    std::cout << "Throughput:       " << std::fixed << std::setprecision(2) << ops << " orders/sec" << std::endl;
    std::cout << "Trades Executed:  " << book.getTrades().size() << std::endl;
    std::cout << "--------------------------------" << std::endl;

    return 0;
}