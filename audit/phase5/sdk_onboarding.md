# Phase 5 Consumer SDK Onboarding Guide

**SDKs Available**: C++17 (`sdk/cpp/`), Java 20 (`sdk/java/`), Python (`src/mdrap/`)  
**Wire Standard**: 64-Byte SBE v1  
**Date**: 2026-10-09  

---

## 1. C++ Consumer Integration Example

Include `mdrap/consumer.hpp` and link:
```cpp
#include <iostream>
#include "mdrap/consumer.hpp"

int main() {
    mdrap::SbeConsumer consumer([](const mdrap::MarketEvent& evt) {
        std::cout << "Received: " << evt.symbol << " px=" << evt.price 
                  << " seq=" << evt.sequence_number << "\n";
    });

    // Ingest binary stream buffer
    // consumer.process_buffer(buffer, size);
    return 0;
}
```

Build command:
```bash
g++ -std=c++17 -I sdk/cpp/include sdk/cpp/src/consumer.cpp main.cpp -o consumer
```

---

## 2. Java 20 Consumer Integration Example

```java
import com.mdrap.client.SbeConsumer;
import java.nio.ByteBuffer;

public class QuantApp {
    public static void main(String[] args) {
        SbeConsumer consumer = new SbeConsumer(event -> {
            System.out.printf("Event: %s seq=%d px=%.2f%n", 
                event.symbol, event.sequenceNumber, event.price);
        });

        // consumer.process(byteBuffer);
    }
}
```
Compile and run with JDK 17+:
```bash
javac -d build sdk/java/src/com/mdrap/client/*.java QuantApp.java
java -cp build QuantApp
```
