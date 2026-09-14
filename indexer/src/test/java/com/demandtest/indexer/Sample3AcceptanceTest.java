package com.demandtest.indexer;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.IOException;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * SPEC §12, row {@code indexer (Java)} — the definition of done for S0:
 *
 * <blockquote>On a 3-file sample project: every constructor/factory/builder/singleton in the sample
 * appears; {@code callees} of the sample test resolve to the focal method; {@code body_reads_fields}
 * lists the read field.</blockquote>
 *
 * <p>This test is part of the acceptance contract and MUST NOT be weakened. It runs the indexer
 * through its public CLI ({@link Main#main(String[])}) on
 * {@code src/test/resources/sample3}, then asserts the facts of SPEC §4.2 that the rest of the
 * pipeline (demandtest/index.py, demand.py, expand.py) depends on. The sample project itself
 * ({@code sample3/}) is deliberately adversarial: a private constructor, an instance method
 * returning the same type (not a factory), a builder whose {@code build()} returns a *different*
 * type, a static field of another type (not a singleton), a nested enum, a JDK type reached through
 * reflection, a fixture with an initializer, and a test utility method.
 */
class Sample3AcceptanceTest {

    private static final Path SAMPLE = Paths.get("src", "test", "resources", "sample3");
    private static final String WIDGET = "com.sample.Widget";
    private static final String BUILDER = "com.sample.WidgetBuilder";
    private static final String COLOR = "com.sample.Widget.Color";
    private static final String WIDGET_TEST = "com.sample.WidgetTest";
    private static final String WIDGET_FILE = "src/main/java/com/sample/Widget.java";
    private static final String BUILDER_FILE = "src/main/java/com/sample/WidgetBuilder.java";
    private static final String TEST_FILE = "src/test/java/com/sample/WidgetTest.java";
    private static final String FOCAL_SIG = "com.sample.Widget#grow(int)";
    private static final String TEST_ONE = WIDGET_TEST + "#growAddsDeltaToSize";
    private static final String TEST_TWO = WIDGET_TEST + "#growRejectsNegativeDelta";

    @TempDir
    Path tmp;

    private JsonObject index;
    private JsonObject project;
    private JsonObject types;
    private JsonArray tests;

    @BeforeEach
    void indexTheSampleProject() throws IOException {
        Path repo = tmp.resolve("sample3");
        copyTree(SAMPLE, repo);
        Path out = tmp.resolve("index.json");
        // The CLI contract of SPEC §4.1. Main.main must return normally on success (it is called
        // in-process here), so that a green indexer does not take the test JVM down with it.
        Main.main(new String[] {"--repo", repo.toString(), "--out", out.toString()});
        assertTrue(Files.isRegularFile(out), "indexer must write " + out);
        try (Reader reader = Files.newBufferedReader(out, StandardCharsets.UTF_8)) {
            index = JsonParser.parseReader(reader).getAsJsonObject();
        }
        project = index.getAsJsonObject("project");
        types = index.getAsJsonObject("types");
        tests = index.getAsJsonArray("tests");
        assertNotNull(project, "project block");
        assertNotNull(types, "types block");
        assertNotNull(tests, "tests array");
    }

    // ------------------------------------------------------------------ project & shape

    @Test
    void projectBlockDescribesTheSample() {
        assertEquals("maven", str(project, "build_tool"));
        assertEquals("junit5", str(project, "test_framework"));
        assertEquals("junit", str(project, "assertion_lib"));
        assertEquals("none", str(project, "mocking_lib"));
        assertEquals(2, num(project, "n_source_files"), "Widget.java + WidgetBuilder.java");
        assertEquals(1, num(project, "n_test_files"), "WidgetTest.java");
        assertTrue(roots(project, "src_roots").contains("src/main/java"), roots(project, "src_roots").toString());
        assertTrue(roots(project, "test_roots").contains("src/test/java"), roots(project, "test_roots").toString());
    }

    @Test
    void typesHoldMainSourcesOnlyWithDottedNestedNames() {
        assertEquals(
                new TreeSet<>(Set.of(WIDGET, COLOR, BUILDER)),
                new TreeSet<>(types.keySet()),
                "`types` holds the main-source types (nested types dotted); test classes live in `tests`");
        assertEquals("class", str(type(WIDGET), "kind"));
        assertEquals("enum", str(type(COLOR), "kind"));
        assertEquals(WIDGET_FILE, str(type(COLOR), "file"), "nested enum lives in Widget.java");
    }

    // ---------------------------------------------------------------- §12: constructors

    @Test
    void constructorsArePublicAndPackagePrivateOnly() {
        JsonArray ctors = array(type(WIDGET), "ctors");
        assertEquals(2, ctors.size(), "public + package-private; the private one must not be indexed: " + names(ctors));
        for (JsonElement element : ctors) {
            JsonObject ctor = element.getAsJsonObject();
            assertEquals("<init>", str(ctor, "name"));
            paramTypes(ctor); // asserts that every parameter type resolved
            assertFalse("private".equals(str(ctor, "visibility")), "private constructors are excluded (SPEC §4.2)");
            assertEquals(WIDGET_FILE, str(ctor, "file"));
            assertTrue(num(ctor, "line") > 0, "constructors carry a line number");
        }
        JsonObject twoArgs = ctors.get(0).getAsJsonObject();
        assertEquals("public", str(twoArgs, "visibility"));
        assertEquals(List.of("java.lang.String", "int"), paramTypes(twoArgs));
        JsonObject oneArg = ctors.get(1).getAsJsonObject();
        assertEquals("package", str(oneArg, "visibility"));
        assertEquals(List.of("java.lang.String"), paramTypes(oneArg));
    }

    // ------------------------------------------------------ §12: factories and builders

    @Test
    void factoriesAreStaticMethodsReturningTheType() {
        JsonArray factories = array(type(WIDGET), "factories");
        assertEquals(List.of("of"), names(factories),
                "`copy()` is an instance method and `secret()` returns String: neither is a factory");
        JsonObject of = factories.get(0).getAsJsonObject();
        assertEquals(WIDGET, str(of, "returns"));
        assertEquals(List.of("java.lang.String"), paramTypes(of));
        assertTrue(bool(of, "static"));
        assertEquals(WIDGET_FILE, str(of, "file"));
        assertTrue(num(of, "line") > 0);
    }

    @Test
    void buildersAreMethodsReturningABuilderWhoseBuildReturnsTheType() {
        JsonArray builders = array(type(WIDGET), "builders");
        assertEquals(List.of("builder"), names(builders));
        assertEquals(BUILDER, str(builders.get(0).getAsJsonObject(), "returns"));
        // The entry is Widget#builder(), declared in Widget.java: `file` is always the *declaring*
        // file (SPEC §4.2 puts a `file` on every entity), which is also what
        // everyEntityCarriesFileAndLineForInspectedFileAccounting requires.
        assertEquals(WIDGET_FILE, str(builders.get(0).getAsJsonObject(), "file"));

        // WidgetBuilder#build() returns Widget, not WidgetBuilder, so WidgetBuilder has neither
        // builders nor factories of its own — this is the precise reading of SPEC §4.2.
        assertTrue(array(type(BUILDER), "builders").isEmpty(), "build() returns Widget, not WidgetBuilder");
        assertTrue(array(type(BUILDER), "factories").isEmpty(), "name()/size() are instance methods");
        assertEquals(BUILDER_FILE, str(type(BUILDER), "file"), "the builder class is indexed from its own file");
    }

    // ------------------------------------------------------------ §12: singletons

    @Test
    void singletonsAreStaticFieldsOfTheTypeAndEnumConstants() {
        JsonArray singletons = array(type(WIDGET), "singletons");
        assertEquals(List.of("DEFAULT"), names(singletons),
                "VERSION is a static field of type String, and name/size/counter are instance fields");
        JsonObject defaultWidget = singletons.get(0).getAsJsonObject();
        assertEquals(WIDGET, str(defaultWidget, "type"));
        assertTrue(bool(defaultWidget, "static"));
        assertEquals(WIDGET_FILE, str(defaultWidget, "file"));
        assertTrue(num(defaultWidget, "line") > 0);

        JsonArray colors = array(type(COLOR), "singletons");
        assertEquals(List.of("RED", "GREEN"), names(colors), "enum constants in declaration order");
        assertEquals(COLOR, str(colors.get(0).getAsJsonObject(), "type"));
        assertTrue(bool(colors.get(0).getAsJsonObject(), "static"));
    }

    // ------------------------------------------- §12: body_reads_fields (+ writes/throws)

    @Test
    void bodyReadsFieldsListsTheReadField() {
        JsonObject grow = method(WIDGET, "grow");
        assertEquals(List.of("size"), strings(array(grow, "body_reads_fields")),
                "the focal method reads the field size");
        assertTrue(array(grow, "body_writes_fields").isEmpty(), "grow writes no field");
        assertEquals("int", str(grow, "returns"));
        assertEquals(List.of("int"), paramTypes(grow));
        assertEquals("public", str(grow, "visibility"));
        assertTrue(array(grow, "throws").isEmpty());

        JsonObject bump = method(WIDGET, "bump");
        assertTrue(strings(array(bump, "body_writes_fields")).contains("counter"), "bump() writes counter");
    }

    @Test
    void methodsKeepEveryDeclaredMethodIncludingPrivateOnesAndThrowsClauses() {
        JsonObject secret = method(WIDGET, "secret");
        assertEquals("private", str(secret, "visibility"));
        assertTrue(bool(secret, "static"));

        JsonObject measure = method(WIDGET, "measure");
        assertEquals(List.of("java.io.IOException"), strings(array(measure, "throws")),
                "throws clauses are resolved FQNs");
        assertEquals(List.of("java.nio.file.Path"), paramTypes(measure),
                "JDK parameter types resolve through the reflection type solver");
    }

    @Test
    void observablesCoverGettersEqualsHashCodeToString() {
        List<String> observables = names(array(type(WIDGET), "observables"));
        assertTrue(observables.containsAll(List.of("name", "getSize", "equals", "hashCode", "toString")),
                "observables = " + observables);
        assertFalse(observables.contains("grow"), "methods with parameters are not observables");
    }

    @Test
    void everyEntityCarriesFileAndLineForInspectedFileAccounting() {
        for (String fqn : types.keySet()) {
            JsonObject t = types.getAsJsonObject(fqn);
            assertFalse(str(t, "file").isEmpty(), fqn + ".file");
            assertTrue(num(t, "line") > 0, fqn + ".line");
            for (String block : List.of("ctors", "factories", "builders", "singletons", "observables", "methods")) {
                for (JsonElement element : array(t, block)) {
                    JsonObject entity = element.getAsJsonObject();
                    assertEquals(str(t, "file"), str(entity, "file"), fqn + " " + block + " file");
                    assertTrue(num(entity, "line") > 0, fqn + " " + block + " line");
                }
            }
        }
    }

    // ------------------------------------------------------------------- tests block

    @Test
    void testEntitiesDescribeTheSampleTest() {
        assertEquals(2, tests.size(), "both @Test methods are indexed");
        for (String id : List.of(TEST_ONE, TEST_TWO)) {
            JsonObject t = test(id);
            assertEquals(TEST_FILE, str(t, "file"));
            assertEquals(WIDGET_TEST, str(t, "class"));
            assertEquals("junit5", str(t, "framework"));
            assertEquals("junit", str(t, "assertion_lib"));
            assertEquals("none", str(t, "mocking"));
            assertTrue(num(t, "line_start") > 0, id + ".line_start");
            assertTrue(num(t, "line_end") >= num(t, "line_start"), id + ".line_end");
            assertTrue(str(t, "source").contains("grow("), id + ".source is the method text");
            assertTrue(strings(array(t, "callees")).contains(FOCAL_SIG),
                    id + " callees = " + strings(array(t, "callees")) + " (SPEC §12: must resolve to the focal method)");
        }
    }

    @Test
    void fixturesCarryTheirInitializerText() {
        JsonArray fixtures = array(test(TEST_ONE), "fixtures");
        assertEquals(List.of("widget"), names(fixtures));
        JsonObject widget = fixtures.get(0).getAsJsonObject();
        assertEquals(WIDGET, str(widget, "type"));
        assertFalse(bool(widget, "static"));
        assertEquals(TEST_FILE, str(widget, "file"));
        assertFalse(widget.get("initializer").isJsonNull(), "fixture initializer text is kept");
        assertTrue(widget.get("initializer").getAsString().contains("of("),
                "initializer = " + widget.get("initializer").getAsString());
    }

    @Test
    void testHelpersRegistryIsAnObjectKeyedByTestFqn() {
        JsonObject helpers = index.getAsJsonObject("test_helpers");
        assertNotNull(helpers, "test_helpers must exist as a JSON object (demandtest/index.py §5)");
        assertTrue(helpers.has(WIDGET_TEST), "test_helpers keys are test-source type FQNs: " + helpers.keySet());
        JsonObject widgetNamed = null;
        for (JsonElement element : helpers.getAsJsonArray(WIDGET_TEST)) {
            if ("widgetNamed".equals(str(element.getAsJsonObject(), "name"))) {
                widgetNamed = element.getAsJsonObject();
            }
        }
        assertNotNull(widgetNamed, "non-void, non-private test-utility methods are Helper recipes");
        assertEquals(WIDGET, str(widgetNamed, "returns"));
        assertTrue(bool(widgetNamed, "static"));
        // demandtest/index.py parses these entries as Methods: the dict form is mandatory, a JSON
        // array would break the loader at index.py:305.
        assertTrue(helpers.get(WIDGET_TEST).isJsonArray());
    }

    // -------------------------------------------------------------------- plumbing

    private JsonObject type(String fqn) {
        assertTrue(types.has(fqn), "missing type " + fqn + "; indexed: " + types.keySet());
        return types.getAsJsonObject(fqn);
    }

    private JsonObject method(String owner, String name) {
        for (JsonElement element : array(type(owner), "methods")) {
            if (name.equals(str(element.getAsJsonObject(), "name"))) {
                return element.getAsJsonObject();
            }
        }
        throw new AssertionError("no method " + name + " in " + owner + "; got " + names(array(type(owner), "methods")));
    }

    private JsonObject test(String id) {
        for (JsonElement element : tests) {
            if (id.equals(str(element.getAsJsonObject(), "id"))) {
                return element.getAsJsonObject();
            }
        }
        throw new AssertionError("no test " + id + " in " + names(tests));
    }

    private static JsonArray array(JsonObject owner, String key) {
        assertTrue(owner.has(key), "missing key " + key + " in " + owner.keySet());
        assertTrue(owner.get(key).isJsonArray(), key + " must be a JSON array");
        return owner.getAsJsonArray(key);
    }

    private static String str(JsonObject owner, String key) {
        assertTrue(owner.has(key), "missing key " + key + " in " + owner.keySet());
        assertFalse(owner.get(key).isJsonNull(), key + " must not be null");
        return owner.get(key).getAsString();
    }

    private static int num(JsonObject owner, String key) {
        assertTrue(owner.has(key), "missing key " + key + " in " + owner.keySet());
        assertFalse(owner.get(key).isJsonNull(), key + " must not be null");
        return owner.get(key).getAsInt();
    }

    private static boolean bool(JsonObject owner, String key) {
        assertTrue(owner.has(key), "missing key " + key + " in " + owner.keySet());
        return owner.get(key).getAsBoolean();
    }

    private static List<String> names(JsonArray array) {
        List<String> out = new ArrayList<>();
        for (JsonElement element : array) {
            out.add(str(element.getAsJsonObject(), "name"));
        }
        return out;
    }

    private static List<String> strings(JsonArray array) {
        List<String> out = new ArrayList<>();
        for (JsonElement element : array) {
            out.add(element.getAsString());
        }
        return out;
    }

    /** Parameter types, asserting that each one resolved (SPEC §4.2: {@code "resolved": true}). */
    private static List<String> paramTypes(JsonObject method) {
        List<String> out = new ArrayList<>();
        for (JsonElement element : array(method, "params")) {
            JsonObject param = element.getAsJsonObject();
            assertTrue(bool(param, "resolved"), "unresolved parameter type in the sample: " + param);
            out.add(str(param, "type"));
        }
        return out;
    }

    /** Root lists are compared leniently: trailing slashes and {@code ./} are ignored. */
    private static List<String> roots(JsonObject owner, String key) {
        List<String> out = new ArrayList<>();
        for (String root : strings(array(owner, key))) {
            String normalized = root.replace('\\', '/');
            while (normalized.startsWith("./")) {
                normalized = normalized.substring(2);
            }
            while (normalized.endsWith("/")) {
                normalized = normalized.substring(0, normalized.length() - 1);
            }
            out.add(normalized);
        }
        return out;
    }

    private static void copyTree(Path from, Path to) throws IOException {
        assertTrue(Files.isDirectory(from), "sample project missing at " + from.toAbsolutePath());
        Files.walkFileTree(from, new SimpleFileVisitor<Path>() {
            @Override
            public FileVisitResult preVisitDirectory(Path dir, BasicFileAttributes attrs) throws IOException {
                Files.createDirectories(to.resolve(from.relativize(dir).toString()));
                return FileVisitResult.CONTINUE;
            }

            @Override
            public FileVisitResult visitFile(Path file, BasicFileAttributes attrs) throws IOException {
                Files.copy(file, to.resolve(from.relativize(file).toString()));
                return FileVisitResult.CONTINUE;
            }
        });
    }
}
