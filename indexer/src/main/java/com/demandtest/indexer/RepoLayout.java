package com.demandtest.indexer;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.stream.Stream;

/**
 * Root and build-tool detection (brief §3), plus the sorted file walk.
 *
 * <p>Order: explicit {@code --src-roots}/{@code --test-roots}; else a Maven POM walk that follows
 * {@code <modules>} recursively and takes every existing {@code <module>/src/main/java} and
 * {@code <module>/src/test/java}; else Gradle conventions; else plain {@code src/main/java} /
 * {@code src/test/java} with {@code build_tool = "unknown"}.
 */
final class RepoLayout {

    /** A {@code .java} file under one of the roots, with its repository-relative path. */
    record JavaFile(Path path, String relPath, boolean testRoot) {}

    final Path repo;
    final String buildTool;
    final List<Path> srcRoots;
    final List<Path> testRoots;
    final Pom rootPom;

    private RepoLayout(Path repo, String buildTool, List<Path> srcRoots, List<Path> testRoots, Pom rootPom) {
        this.repo = repo;
        this.buildTool = buildTool;
        this.srcRoots = srcRoots;
        this.testRoots = testRoots;
        this.rootPom = rootPom;
    }

    static RepoLayout detect(Path repo, List<String> explicitSrc, List<String> explicitTest, ProblemLog log) {
        Pom rootPom = Pom.parse(repo.resolve("pom.xml"));
        boolean gradle = Files.isRegularFile(repo.resolve("build.gradle"))
                || Files.isRegularFile(repo.resolve("build.gradle.kts"));
        String buildTool = rootPom != null ? "maven" : (gradle ? "gradle" : "unknown");

        List<Path> detectedSrc = new ArrayList<>();
        List<Path> detectedTest = new ArrayList<>();
        if (rootPom != null) {
            walkMavenModules(repo, new HashSet<>(), detectedSrc, detectedTest);
        } else {
            addIfDirectory(repo.resolve("src/main/java"), detectedSrc);
            addIfDirectory(repo.resolve("src/test/java"), detectedTest);
        }

        List<Path> src = explicitSrc.isEmpty() ? detectedSrc : resolveRoots(repo, explicitSrc, log);
        List<Path> test = explicitTest.isEmpty() ? detectedTest : resolveRoots(repo, explicitTest, log);
        if (!explicitSrc.isEmpty() || !explicitTest.isEmpty()) {
            log.warn("explicit roots in use (src=" + src.size() + ", test=" + test.size() + ")");
        }
        return new RepoLayout(repo, buildTool, src, test, rootPom);
    }

    /** Follows {@code <modules>} recursively; the root module is included. Cycle-guarded. */
    private static void walkMavenModules(Path moduleDir, Set<Path> visited, List<Path> src, List<Path> test) {
        Path key = moduleDir.toAbsolutePath().normalize();
        if (!visited.add(key)) {
            return;
        }
        addIfDirectory(moduleDir.resolve("src/main/java"), src);
        addIfDirectory(moduleDir.resolve("src/test/java"), test);
        Pom pom = Pom.parse(moduleDir.resolve("pom.xml"));
        if (pom == null) {
            return;
        }
        for (String module : pom.modules) {
            if (module.contains("${")) {
                continue; // unresolved property (e.g. <module>${some.dir}</module>)
            }
            Path child = moduleDir.resolve(module).normalize();
            if (Files.isDirectory(child)) {
                walkMavenModules(child, visited, src, test);
            }
        }
    }

    private static void addIfDirectory(Path candidate, List<Path> roots) {
        if (Files.isDirectory(candidate)) {
            Path normalized = candidate.toAbsolutePath().normalize();
            if (!roots.contains(normalized)) {
                roots.add(normalized);
            }
        }
    }

    private static List<Path> resolveRoots(Path repo, List<String> entries, ProblemLog log) {
        List<Path> out = new ArrayList<>();
        Set<Path> seen = new LinkedHashSet<>();
        for (String entry : entries) {
            if (entry == null || entry.isBlank()) {
                continue;
            }
            Path path = Paths.get(entry.trim());
            if (!path.isAbsolute()) {
                path = repo.resolve(path);
            }
            path = path.normalize();
            if (!Files.isDirectory(path)) {
                log.warn("ignoring root that is not a directory: " + entry);
                continue;
            }
            if (seen.add(path)) {
                out.add(path);
            }
        }
        return out;
    }

    /** Repository-relative path with forward slashes (SPEC §2.1). */
    String relative(Path path) {
        try {
            if (path.startsWith(repo)) {
                return repo.relativize(path).toString().replace('\\', '/');
            }
        } catch (RuntimeException ignored) {
            // different root (e.g. an absolute --src-roots outside the repository)
        }
        return path.toAbsolutePath().normalize().toString().replace('\\', '/');
    }

    List<String> srcRootStrings() {
        return relativeRoots(srcRoots);
    }

    List<String> testRootStrings() {
        return relativeRoots(testRoots);
    }

    private List<String> relativeRoots(List<Path> roots) {
        List<String> out = new ArrayList<>();
        for (Path root : roots) {
            String value = relative(root);
            while (value.endsWith("/")) {
                value = value.substring(0, value.length() - 1);
            }
            out.add(value);
        }
        return out;
    }

    /** All {@code .java} files of the source roots then the test roots, sorted, deduplicated. */
    List<JavaFile> discoverJavaFiles(ProblemLog log) {
        List<JavaFile> out = new ArrayList<>();
        Set<Path> seen = new HashSet<>();
        collect(srcRoots, false, out, seen, log);
        collect(testRoots, true, out, seen, log);
        return out;
    }

    private void collect(List<Path> roots, boolean testRoot, List<JavaFile> out, Set<Path> seen, ProblemLog log) {
        for (Path root : roots) {
            List<Path> files = new ArrayList<>();
            try (Stream<Path> stream = Files.walk(root)) {
                stream.filter(Files::isRegularFile)
                        .filter(p -> p.getFileName().toString().endsWith(".java"))
                        .forEach(files::add);
            } catch (Exception failure) {
                log.warn("cannot walk " + relative(root) + ": " + failure);
                continue;
            }
            files.sort(Comparator.comparing(p -> p.toString()));
            for (Path file : files) {
                Path key = file.toAbsolutePath().normalize();
                if (seen.add(key)) {
                    out.add(new JavaFile(key, relative(key), testRoot));
                }
            }
        }
    }
}
