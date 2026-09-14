package com.demandtest.indexer;

import java.io.PrintStream;
import java.util.LinkedHashSet;
import java.util.Set;

/**
 * Diagnostics channel. The indexer never aborts on a single bad file (brief §7): every recoverable
 * problem is reported here and the run continues, with unresolved symbols counted so the failure
 * rate stays measurable (SPEC §4.2).
 */
final class ProblemLog {

    private static final int MAX_EXAMPLES = 20;

    private final PrintStream err;
    private final Set<String> unresolvedExamples = new LinkedHashSet<>();
    private int unresolvedCount;

    ProblemLog(PrintStream err) {
        this.err = err;
    }

    void warn(String message) {
        err.println("indexer: " + message);
    }

    /** A symbol (type, return type, supertype, callee) that could not be resolved. */
    void unresolved(String symbol) {
        unresolvedCount++;
        if (unresolvedExamples.size() < MAX_EXAMPLES) {
            unresolvedExamples.add(symbol);
        }
    }

    int unresolvedCount() {
        return unresolvedCount;
    }

    Set<String> unresolvedExamples() {
        return unresolvedExamples;
    }
}
