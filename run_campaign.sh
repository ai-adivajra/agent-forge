#!/usr/bin/env bash
# run_campaign.sh — Run each faithfulness golden case N times, report PASS rate.
#
# Usage:
#   ./run_campaign.sh before   # save results as baseline_before.txt
#   ./run_campaign.sh after    # save results as baseline_after.txt
#   ./run_campaign.sh compare  # print before/after PASS rates side by side

CASES="008-no-entity-substitution 011a-no-model-invention 011b-no-command-invention 011c-no-file-invention 012-no-status-conversion 013-summary-entity-consistency"
RUNS=10
RESULTS_DIR="campaign_results"
mkdir -p "$RESULTS_DIR"

run_campaign() {
    local label="$1"
    local outfile="$RESULTS_DIR/${label}.txt"
    : > "$outfile"

    for c in $CASES; do
        echo "===== $c =====" | tee -a "$outfile"
        pass=0
        for i in $(seq 1 $RUNS); do
            result=$(python validate.py --case "unit/$c" 2>&1)
            line=$(printf '%s\n' "$result" | grep -E '✓|✗|!' | head -1)
            if printf '%s\n' "$line" | grep -q "PASS"; then
                pass=$((pass + 1))
            fi
            echo "  run $i: $line" >> "$outfile"
        done
        echo "  -> $c : $pass/$RUNS PASS" | tee -a "$outfile"
        echo "" | tee -a "$outfile"
    done

    echo "Results saved to $outfile"
}

compare() {
    local before="$RESULTS_DIR/before.txt"
    local after="$RESULTS_DIR/after.txt"

    if [ ! -f "$before" ] || [ ! -f "$after" ]; then
        echo "Missing baseline files. Run './run_campaign.sh before' and './run_campaign.sh after' first."
        exit 1
    fi

    echo "================================================================"
    echo "  Faithfulness v2.1 - Before / After comparison"
    echo "================================================================"
    printf "%-40s %12s %12s\n" "Case" "Before" "After"
    echo "----------------------------------------------------------------"

    for c in $CASES; do
        b=$(grep -- "-> $c :" "$before" | awk '{print $4}')
        a=$(grep -- "-> $c :" "$after"  | awk '{print $4}')
        printf "%-40s %12s %12s\n" "$c" "${b:-N/A}" "${a:-N/A}"
    done
    echo "================================================================"
}

case "${1:-}" in
    before)  run_campaign "before" ;;
    after)   run_campaign "after" ;;
    compare) compare ;;
    *)
        echo "Usage: $0 {before|after|compare}"
        exit 1
        ;;
esac
