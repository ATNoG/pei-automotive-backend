#!/bin/bash

cd "$(dirname "$0")/.." || exit

echo "Which test do you want to run?"
# Automatically lists all test files in the tests/ directory
TEST_FILES=(tests/test_*.py "All")

select TEST_CHOICE in "${TEST_FILES[@]}"; do
    if [[ -n "$TEST_CHOICE" ]]; then
        break
    else
        echo "Invalid choice."
    fi
done

TARGET=$TEST_CHOICE
if [[ "$TARGET" == "All" ]]; then
    TARGET="tests/"
else
    # Extract individual test functions from the selected file
    TESTS=($(grep -o "^def test_[a-zA-Z_]*" "$TARGET" | sed 's/def //'))
    
    if [[ ${#TESTS[@]} -gt 1 ]]; then
        echo ""
        echo "Found ${#TESTS[@]} tests in $(basename $TARGET):"
        TESTS+=("All")
        
        select TEST_FUNC in "${TESTS[@]}"; do
            if [[ -n "$TEST_FUNC" ]]; then
                break
            else
                echo "Invalid choice."
            fi
        done
        
        if [[ "$TEST_FUNC" != "All" ]]; then
            TARGET="$TARGET::$TEST_FUNC"
        fi
    elif [[ ${#TESTS[@]} -eq 1 ]]; then
        echo "Running test: ${TESTS[0]}"
    fi
fi

read -p "How many times do you want to run the test? " NUM_RUNS

if ! [[ "$NUM_RUNS" =~ ^[0-9]+$ ]]; then
    echo "Invalid number."
    exit 1
fi

# Variables to track successes and failures
PASSED=0
FAILED=0

for ((i=1; i<=NUM_RUNS; i++)); do
    echo "Run $i/$NUM_RUNS: $TARGET"
    
    if pytest "$TARGET" -q; then
        ((PASSED++))
    else
        ((FAILED++))
    fi
done

echo ""
echo "Results: $PASSED passed, $FAILED failed."
