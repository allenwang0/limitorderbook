// Replays an order script against MapBook and writes one JSON line per
// command: the command, the events it produced and depth(10) afterwards.
//
// Script lines (blank lines and lines starting with '#' are skipped):
//   ADD <id> <B|S> <price> <qty> [LIMIT|MARKET|IOC|FOK|POST]
//   CXL <id>
//   MOD <id> <qty> <price>

#include "lob/MapBook.h"

#include <exception>
#include <fstream>
#include <iostream>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

namespace {

using namespace lob;

constexpr std::size_t kDepthLevels = 10;

struct CollectingSink : EventSink {
    std::vector<Event> events;
    void onEvent(const Event& event) override { events.push_back(event); }
};

const char* typeName(EventType type) {
    switch (type) {
    case EventType::Accepted:
        return "accepted";
    case EventType::Fill:
        return "fill";
    case EventType::Cancelled:
        return "cancelled";
    case EventType::Rejected:
        return "rejected";
    }
    return "unknown";
}

const char* reasonName(RejectReason reason) {
    switch (reason) {
    case RejectReason::None:
        return "none";
    case RejectReason::ZeroQuantity:
        return "zero_quantity";
    case RejectReason::InvalidPrice:
        return "invalid_price";
    case RejectReason::DuplicateId:
        return "duplicate_id";
    case RejectReason::UnknownId:
        return "unknown_id";
    case RejectReason::WouldCross:
        return "would_cross";
    }
    return "unknown";
}

std::optional<OrderType> parseType(const std::string& text) {
    if (text == "LIMIT") {
        return OrderType::Limit;
    }
    if (text == "MARKET") {
        return OrderType::Market;
    }
    if (text == "IOC") {
        return OrderType::ImmediateOrCancel;
    }
    if (text == "FOK") {
        return OrderType::FillOrKill;
    }
    if (text == "POST") {
        return OrderType::PostOnly;
    }
    return std::nullopt;
}

std::string jsonEscape(const std::string& text) {
    std::string out;
    for (const char c : text) {
        if (c == '"' || c == '\\') {
            out += '\\';
        }
        out += c;
    }
    return out;
}

void writeLevels(std::ostream& out, const std::vector<LevelInfo>& levels) {
    out << '[';
    for (std::size_t i = 0; i < levels.size(); ++i) {
        out << (i ? "," : "") << '[' << levels[i].price << ',' << levels[i].quantity << ','
            << levels[i].orderCount << ']';
    }
    out << ']';
}

void writeLine(std::ostream& out, std::size_t lineNumber, const std::string& command,
               const std::vector<Event>& events, const MapBook& book) {
    out << "{\"line\":" << lineNumber << ",\"cmd\":\"" << jsonEscape(command) << "\",\"events\":[";
    for (std::size_t i = 0; i < events.size(); ++i) {
        const Event& e = events[i];
        out << (i ? "," : "") << "{\"type\":\"" << typeName(e.type) << "\",\"id\":" << e.orderId
            << ",\"side\":\"" << (e.side == Side::Buy ? 'B' : 'S') << "\",\"price\":" << e.price
            << ",\"qty\":" << e.quantity;
        if (e.type == EventType::Fill) {
            out << ",\"resting_id\":" << e.restingId;
        }
        if (e.type == EventType::Rejected) {
            out << ",\"reason\":\"" << reasonName(e.reason) << '"';
        }
        out << '}';
    }
    out << "],\"bids\":";
    writeLevels(out, book.depth(Side::Buy, kDepthLevels));
    out << ",\"asks\":";
    writeLevels(out, book.depth(Side::Sell, kDepthLevels));
    out << "}\n";
}

// Applies one script line. Returns an error message, or nothing on success.
std::optional<std::string> apply(MapBook& book, const std::string& line) {
    std::istringstream in(line);
    std::string verb;
    in >> verb;
    if (verb == "ADD") {
        OrderId id = 0;
        std::string side;
        Price price = 0;
        Quantity quantity = 0;
        std::string type = "LIMIT";
        if (!(in >> id >> side >> price >> quantity) || (side != "B" && side != "S")) {
            return "expected ADD <id> <B|S> <price> <qty> [type]";
        }
        in >> type;
        const auto orderType = parseType(type);
        if (!orderType) {
            return "unknown order type '" + type + "'";
        }
        book.addOrder({id, side == "B" ? Side::Buy : Side::Sell, price, quantity, *orderType});
    } else if (verb == "CXL") {
        OrderId id = 0;
        if (!(in >> id)) {
            return "expected CXL <id>";
        }
        book.cancelOrder(id);
    } else if (verb == "MOD") {
        OrderId id = 0;
        Quantity quantity = 0;
        Price price = 0;
        if (!(in >> id >> quantity >> price)) {
            return "expected MOD <id> <qty> <price>";
        }
        book.modifyOrder(id, quantity, price);
    } else {
        return "unknown command '" + verb + "'";
    }
    std::string extra;
    if (in >> extra) {
        return "unexpected trailing text '" + extra + "'";
    }
    return std::nullopt;
}

int run(int argc, char** argv) {
    if (argc > 2) {
        std::cerr << "usage: lob_replay [script]   (reads stdin when no script is given)\n";
        return 2;
    }
    std::ifstream file;
    if (argc == 2) {
        file.open(argv[1]);
        if (!file) {
            std::cerr << "lob_replay: cannot open " << argv[1] << '\n';
            return 2;
        }
    }
    std::istream& input = argc == 2 ? file : std::cin;

    CollectingSink sink;
    MapBook book(sink);
    std::string line;
    std::size_t lineNumber = 0;
    while (std::getline(input, line)) {
        ++lineNumber;
        const auto first = line.find_first_not_of(" \t\r");
        if (first == std::string::npos || line[first] == '#') {
            continue;
        }
        const auto last = line.find_last_not_of(" \t\r");
        const std::string command = line.substr(first, last - first + 1);
        sink.events.clear();
        if (const auto error = apply(book, command)) {
            std::cerr << "lob_replay: line " << lineNumber << ": " << *error << '\n';
            return 1;
        }
        writeLine(std::cout, lineNumber, command, sink.events, book);
    }
    return 0;
}

} // namespace

int main(int argc, char** argv) {
    try {
        return run(argc, argv);
    } catch (const std::exception& error) {
        std::cerr << "lob_replay: " << error.what() << '\n';
        return 1;
    }
}
