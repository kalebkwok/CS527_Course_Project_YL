package com.demandtest.indexer;

import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;

/**
 * Command line of SPEC §4.1 / brief §2:
 *
 * <pre>
 * java -jar indexer.jar --repo &lt;path&gt; --out &lt;index.json&gt; [--src-roots a,b] [--test-roots c,d]
 *                       [--classpath-file &lt;path&gt;] [--maven-classpath]
 * </pre>
 *
 * {@code --src-roots}/{@code --test-roots} are repeatable and accept {@code --flag=value}. Unknown
 * arguments, a missing value or a missing required option raise {@link UsageException}, which
 * {@link Main} turns into usage text on stderr and exit code 2.
 */
final class Cli {

    private static final List<String> VALUE_FLAGS =
            List.of("--repo", "--out", "--src-roots", "--test-roots", "--classpath-file");

    static final String USAGE = String.join("\n",
            "usage: java -jar indexer.jar --repo <path> --out <index.json> [options]",
            "",
            "  --repo <path>            existing Java repository directory (required)",
            "  --out <path>             output index.json path; parent directories are created (required)",
            "  --src-roots <a,b>        source roots, comma-separated, relative to --repo (repeatable)",
            "  --test-roots <c,d>       test roots, comma-separated, relative to --repo (repeatable)",
            "  --classpath-file <path>  dependency classpath for the symbol solver, one entry per",
            "                           line or ':'-separated (optional)",
            "  --maven-classpath        run `mvn -o -q dependency:build-classpath` first (slow, off",
            "                           by default so the acceptance check stays hermetic)",
            "  -h, --help               print this help");

    private Cli() {}

    static Options parse(String[] args) throws UsageException {
        Options options = new Options();
        int index = 0;
        while (index < args.length) {
            String argument = args[index++];
            String inlineValue = null;
            int equals = argument.indexOf('=');
            if (argument.startsWith("--") && equals > 0) {
                inlineValue = argument.substring(equals + 1);
                argument = argument.substring(0, equals);
            }
            boolean takesValue = VALUE_FLAGS.contains(argument);
            String value = inlineValue;
            if (value == null && takesValue) {
                if (index >= args.length) {
                    throw new UsageException("missing value for " + argument);
                }
                value = args[index++];
            }
            if (value != null && !takesValue) {
                throw new UsageException("argument does not take a value: " + argument);
            }
            switch (argument) {
                case "-h", "--help" -> options.help = true;
                case "--maven-classpath" -> options.mavenClasspath = true;
                case "--repo" -> options.repo = Paths.get(value);
                case "--out" -> options.out = Paths.get(value);
                case "--src-roots" -> addRoots(options.srcRoots, value);
                case "--test-roots" -> addRoots(options.testRoots, value);
                case "--classpath-file" -> options.classpathFile = Paths.get(value);
                default -> throw new UsageException("unknown argument: " + argument);
            }
        }
        if (options.help) {
            return options;
        }
        if (options.repo == null) {
            throw new UsageException("missing required option --repo");
        }
        if (options.out == null) {
            throw new UsageException("missing required option --out");
        }
        return options;
    }

    private static void addRoots(List<String> target, String value) {
        for (String part : value.split(",")) {
            if (!part.isBlank()) {
                target.add(part.trim());
            }
        }
    }

    static final class Options {
        Path repo;
        Path out;
        final List<String> srcRoots = new ArrayList<>();
        final List<String> testRoots = new ArrayList<>();
        Path classpathFile;
        boolean mavenClasspath;
        boolean help;
    }

    static final class UsageException extends Exception {
        UsageException(String message) {
            super(message);
        }
    }
}
