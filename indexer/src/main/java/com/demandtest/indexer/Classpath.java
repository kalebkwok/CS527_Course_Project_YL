package com.demandtest.indexer;

import com.github.javaparser.ParserConfiguration;
import com.github.javaparser.symbolsolver.resolution.typesolvers.ClassLoaderTypeSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.CombinedTypeSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.JavaParserTypeSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.ReflectionTypeSolver;
import java.io.File;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.regex.Pattern;

/**
 * Symbol-solver wiring (brief §7): {@code ReflectionTypeSolver} for the JDK, one
 * {@code JavaParserTypeSolver} per source/test root, and a classloader solver over the dependency
 * jars from {@code --classpath-file} / {@code --maven-classpath} plus the modules'
 * {@code target/classes} and {@code target/test-classes} directories when they exist.
 */
final class Classpath {

    private Classpath() {}

    /** One entry per line, and every line may itself be {@code :}-separated. Missing entries are skipped. */
    static List<Path> fromFile(Path classpathFile, ProblemLog log) {
        if (classpathFile == null) {
            return new ArrayList<>();
        }
        if (!Files.isRegularFile(classpathFile)) {
            log.warn("classpath file not found: " + classpathFile);
            return new ArrayList<>();
        }
        try {
            return fromText(Files.readString(classpathFile, StandardCharsets.UTF_8), log);
        } catch (Exception failure) {
            log.warn("cannot read classpath file " + classpathFile + ": " + failure);
            return new ArrayList<>();
        }
    }

    private static List<Path> fromText(String text, ProblemLog log) {
        Set<Path> out = new LinkedHashSet<>();
        for (String line : text.split("\\R")) {
            for (String entry : line.split(Pattern.quote(File.pathSeparator))) {
                String trimmed = entry.trim();
                if (trimmed.isEmpty()) {
                    continue;
                }
                Path path = Paths.get(trimmed);
                if (Files.exists(path)) {
                    out.add(path);
                } else {
                    log.warn("classpath entry not found: " + trimmed);
                }
            }
        }
        return new ArrayList<>(out);
    }

    /**
     * Opt-in {@code mvn -o -q dependency:build-classpath} (≤ 120 s). Every failure is ignored: the
     * index is still useful without dependency types, it just records more unresolved symbols.
     */
    static List<Path> fromMaven(Path repo, ProblemLog log) {
        Path output = null;
        try {
            output = Files.createTempFile("demandtest-maven-classpath", ".txt");
            ProcessBuilder builder = new ProcessBuilder("mvn", "-o", "-q", "dependency:build-classpath",
                    "-Dmdep.outputFile=" + output.toAbsolutePath());
            builder.directory(repo.toFile());
            builder.redirectErrorStream(true);
            builder.redirectOutput(ProcessBuilder.Redirect.DISCARD);
            Process process = builder.start();
            if (!process.waitFor(120, TimeUnit.SECONDS)) {
                process.destroyForcibly();
                log.warn("mvn dependency:build-classpath timed out after 120s");
                return new ArrayList<>();
            }
            if (process.exitValue() != 0) {
                log.warn("mvn dependency:build-classpath failed with exit code " + process.exitValue());
                return new ArrayList<>();
            }
            return fromText(Files.readString(output, StandardCharsets.UTF_8), log);
        } catch (Throwable failure) {
            log.warn("mvn dependency:build-classpath unavailable: " + failure);
            return new ArrayList<>();
        } finally {
            if (output != null) {
                try {
                    Files.deleteIfExists(output);
                } catch (Exception ignored) {
                    // best effort
                }
            }
        }
    }

    static CombinedTypeSolver solver(List<Path> sourceRoots, List<Path> classpathEntries,
                                     List<Path> classDirectories, ProblemLog log) {
        CombinedTypeSolver solvers = new CombinedTypeSolver();
        solvers.add(new ReflectionTypeSolver()); // JDK only: java.lang.String, java.util.*, ...
        ParserConfiguration inner = new ParserConfiguration()
                .setLanguageLevel(ParserConfiguration.LanguageLevel.JAVA_17)
                .setCharacterEncoding(StandardCharsets.UTF_8);
        for (Path root : sourceRoots) {
            if (!Files.isDirectory(root)) {
                continue;
            }
            try {
                solvers.add(new JavaParserTypeSolver(root, inner));
            } catch (Throwable failure) {
                log.warn("cannot use source root " + root + " in the symbol solver: " + failure);
            }
        }
        List<URL> urls = new ArrayList<>();
        for (Path entry : classpathEntries) {
            addUrl(urls, entry, log);
        }
        for (Path entry : classDirectories) {
            addUrl(urls, entry, log);
        }
        if (!urls.isEmpty()) {
            ClassLoader loader = new URLClassLoader(urls.toArray(new URL[0]), ClassLoader.getPlatformClassLoader());
            solvers.add(new ClassLoaderTypeSolver(loader));
        }
        return solvers;
    }

    private static void addUrl(List<URL> urls, Path entry, ProblemLog log) {
        if (entry == null || !Files.exists(entry)) {
            return;
        }
        try {
            urls.add(entry.toUri().toURL());
        } catch (Exception failure) {
            log.warn("cannot add " + entry + " to the symbol solver classpath: " + failure);
        }
    }
}
