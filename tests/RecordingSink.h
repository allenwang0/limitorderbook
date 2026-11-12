#pragma once

#include "lob/Types.h"

#include <vector>

namespace lob::test {

struct RecordingSink : EventSink {
    std::vector<Event> events;

    void onEvent(const Event& event) override { events.push_back(event); }

    std::vector<Event> ofType(EventType type) const {
        std::vector<Event> out;
        for (const Event& event : events) {
            if (event.type == type) {
                out.push_back(event);
            }
        }
        return out;
    }
};

} // namespace lob::test
