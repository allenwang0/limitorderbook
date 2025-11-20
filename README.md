# Python Limit Order Book

A fast, pure Python implementation of a Limit Order Book (LOB) designed for financial trading simulations and algorithmic trading research. This engine implements a standard **Price-Time Priority** matching algorithm.

## 📖 Overview

The Limit Order Book is the core of any trading exchange. It manages all buy and sell orders, matches them when prices cross, and maintains the state of the market.

This implementation focuses on algorithmic efficiency, utilizing a combination of:
1.  **Red-Black Trees** for price levels (sparse storage).
2.  **Doubly Linked Lists** for order queues at each price level.
3.  **Hash Maps** for O(1) order lookups.


## ✨ Features

* **Price-Time Priority:** Orders are matched first by price (best price wins), then by time (earliest order wins).
* **Fast Operations:** Optimized data structures to ensure low latency for adding, canceling, and executing orders.
* **Market & Limit Orders:** Supports standard order types.
* **Depth Management:** Efficiently tracks volume at every price level.
* **Order Lookup:** Instant access to any order state via internal hashmaps.

## 🛠 Architecture & Complexity

To achieve optimal performance in Python, the book is structured as follows:

### Data Structures
* **`Order`**: Represents a distinct order with quantity, price, and ID.
* **`OrderList`**: A Doubly Linked List containing all orders at a specific price. This maintains the "Time" priority.
* **`OrderTree`**: A Red-Black Tree (AVL or RB logic) that stores `OrderList` objects sorted by price.
* **`OrderBook`**: The manager class containing two `OrderTrees` (one for Bids, one for Asks) and a Hash Map pointing to specific orders.

### Big-O Time Complexity

| Operation | Complexity | Description |
| :--- | :--- | :--- |
| **Add Order** | O(log M) | Where M is the number of price levels (Tree traversal). |
| **Cancel Order** | O(1) | Direct access via Hash Map and Doubly Linked List removal. |
| **Get Best Bid/Ask**| O(1) | Cached pointers to min/max nodes in the Tree. |
| **Volume at Price** | O(1) | Maintained within the OrderList node. |

## 📦 Installation

1.  **Clone the repository:**
    ```bash
    git clone [https://github.com/allenwang0/limitorderbook.git](https://github.com/allenwang0/limitorderbook.git)
    cd limitorderbook
    ```

2.  **Install dependencies:**
    This project relies on the `bintrees` library for the Red-Black tree implementation.
    ```bash
    pip install bintrees
    ```

## 🚀 Usage

### Basic Example

Here is how to initialize the book, process orders, and inspect the market state.

```python
from limitorderbook import OrderBook

# Initialize the Order Book
book = OrderBook()

# Create some Limit Orders
# Arguments: (Quote(inside, side, id, quantity, price), timestamp)
# Side: 'bid' (buy) or 'ask' (sell)

# Add a Buy Order (Bid) at $100 for 10 units
book.process_order({'type': 'limit', 'side': 'bid', 'quantity': 10, 'price': 100, 'trade_id': 101})

# Add a Sell Order (Ask) at $105 for 10 units
book.process_order({'type': 'limit', 'side': 'ask', 'quantity': 10, 'price': 105, 'trade_id': 102})

# Check Best Prices
print(f"Best Bid: {book.get_best_bid()}")
print(f"Best Ask: {book.get_best_ask()}")

# Market Order Execution (Crossing the spread)
# A sell order at $100 will match the existing buy order at $100
trade = {'type': 'limit', 'side': 'ask', 'quantity': 5, 'price': 100, 'trade_id': 103}
book.process_order(trade)

# Check remaining volume on the original bid
# (The original bid of 10 units should now be 5)
print(book.get_volume_at_price('bid', 100))