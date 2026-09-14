package com.demandtest.indexer;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ParserConfiguration;
import com.github.javaparser.symbolsolver.JavaSymbolSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.CombinedTypeSolver;
import java.io.IOException;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

/**
 * S0 — the DemandTest project indexer (SPEC §4). {@code Main.main} returns normally on success and
 * never calls {@code System.exit(0)}: the §12 acceptance test invokes it in-process. Usage errors
 * and fatal failures exit with code 2 (brief §2).
 */
public final class Main {

    private Main() {}

    public static void main(String[] args) {
        int status = run(args, System.out, System.err);
        if (status != 0) {
            System.exit(status);
        }
    }

    /** Runs the indexer and returns the process exit code (0 = success). */
    static int run(String[] args, PrintStream out, PrintStream err) {
        Cli.Options options;
        try {
            options = Cli.parse(args);
        } catch (Cli.UsageException failure) {
            err.println("indexer: " + failure.getMessage());
            err.println();
            err.println(Cli.USAGE);
            return 2;
        }
        if (options.help) {
            out.println(Cli.USAGE);
            return 0;
        }
        ProblemLog log = new ProblemLog(err);
        try {
            index(options, log, err);
            return 0;
        } catch (Throwable failure) {
            err.println("indexer: fatal: " + failure);
            failure.printStackTrace(err);
            return 2;
        }
    }

    private static void index(Cli.Options options, ProblemLog log, PrintStream err) throws IOException {
        Path repo = options.repo.toAbsolutePath().normalize();
        if (!Files.isDirectory(repo)) {
            throw new IllegalArgumentException("--repo is not an existing directory: " + options.repo);
        }
        Path out = options.out.toAbsolutePath().normalize();

        RepoLayout layout = RepoLayout.detect(repo, options.srcRoots, options.testRoots, log);
        Pom pom = layout.rootPom;

        Model.Index index = new Model.Index();
        Model.Project project = index.project;
        project.name = pom != null && pom.artifactId != null && !pom.artifactId.isEmpty()
                ? pom.artifactId
                : String.valueOf(repo.getFileName());
        project.commit = gitHead(repo);
        project.buildTool = layout.buildTool;
        project.javaVersion = pom == null ? null : pom.javaVersion;
        project.srcRoots.addAll(layout.srcRootStrings());
        project.testRoots.addAll(layout.testRootStrings());

        List<RepoLayout.JavaFile> files = layout.discoverJavaFiles(log);
        for (RepoLayout.JavaFile file : files) {
            if (file.testRoot()) {
                project.nTestFiles++;
            } else {
                project.nSourceFiles++;
            }
        }

        List<Path> allRoots = new ArrayList<>(layout.srcRoots);
        allRoots.addAll(layout.testRoots);
        List<Path> classpath = new ArrayList<>();
        if (options.classpathFile != null) {
            classpath.addAll(Classpath.fromFile(options.classpathFile, log));
        }
        if (options.mavenClasspath) {
            classpath.addAll(Classpath.fromMaven(repo, log));
        }
        CombinedTypeSolver solver = Classpath.solver(allRoots, classpath, classDirectories(layout), log);
        ParserConfiguration configuration = new ParserConfiguration()
                .setLanguageLevel(ParserConfiguration.LanguageLevel.JAVA_17)
                .setCharacterEncoding(StandardCharsets.UTF_8)
                .setSymbolResolver(new JavaSymbolSolver(solver));
        JavaParser parser = new JavaParser(configuration);

        new Extractor(pom, log).extract(files, parser, index);
        IndexWriter.write(out, index);

        err.println("indexer: " + files.size() + " files (" + project.nSourceFiles + " source, "
                + project.nTestFiles + " test), " + index.types.size() + " types, " + index.tests.size()
                + " tests -> " + out);
        if (log.unresolvedCount() > 0) {
            err.println("indexer: " + log.unresolvedCount() + " unresolved symbol(s), e.g. "
                    + log.unresolvedExamples());
        }
    }

    /** {@code <module>/target/classes} and {@code target/test-classes} of the conventional layout. */
    private static List<Path> classDirectories(RepoLayout layout) {
        List<Path> out = new ArrayList<>();
        addClassDirectory(layout, layout.srcRoots, "src/main/java", "target/classes", out);
        addClassDirectory(layout, layout.testRoots, "src/test/java", "target/test-classes", out);
        return out;
    }

    private static void addClassDirectory(RepoLayout layout, List<Path> roots, String conventional,
                                          String classes, List<Path> out) {
        for (Path root : roots) {
            String relative = layout.relative(root);
            if (!relative.endsWith(conventional)) {
                continue;
            }
            String module = relative.substring(0, relative.length() - conventional.length());
            Path directory = layout.repo.resolve(module + classes).normalize();
            if (Files.isDirectory(directory) && !out.contains(directory)) {
                out.add(directory);
            }
        }
    }

    /** {@code git -C <repo> rev-parse HEAD}, 10 s timeout; {@code null} when unavailable. */
    private static String gitHead(Path repo) {
        try {
            ProcessBuilder builder = new ProcessBuilder("git", "-C", repo.toString(), "rev-parse", "HEAD");
            builder.redirectErrorStream(true);
            Process process = builder.start();
            if (!process.waitFor(10, TimeUnit.SECONDS)) {
                process.destroyForcibly();
                return null;
            }
            if (process.exitValue() != 0) {
                return null;
            }
            String output = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8).trim();
            int newline = output.indexOf('\n');
            if (newline >= 0) {
                output = output.substring(0, newline).trim();
            }
            return output.isEmpty() ? null : output;
        } catch (Throwable failure) {
            return null;
        }
    }
}
