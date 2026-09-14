package com.demandtest.indexer;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The in-memory index (SPEC §4.2). Plain mutable holders: AST nodes are never retained, so the
 * extractor can drop each compilation unit after it has been converted, and the writer can stream
 * the model out without building a JSON tree (§4.3).
 */
final class Model {

    private Model() {}

    /** {@code Method.params[]}. */
    static final class Param {
        final String name;
        final String type;
        final boolean resolved;

        Param(String name, String type, boolean resolved) {
            this.name = name;
            this.type = type;
            this.resolved = resolved;
        }
    }

    /** {@code Method}. */
    static final class Method {
        String name = "";
        final List<Param> params = new ArrayList<>();
        String returns = "void";
        final List<String> thrown = new ArrayList<>();
        boolean isStatic;
        String visibility = "public";
        String file = "";
        int line;
        int lineEnd;
        final List<String> bodyReadsFields = new ArrayList<>();
        final List<String> bodyWritesFields = new ArrayList<>();
    }

    /** {@code Field}. Enum constants use the same shape. */
    static final class Field {
        String name = "";
        String type = "";
        boolean isStatic;
        boolean isFinal;
        String visibility = "public";
        String file = "";
        int line;
        /** Initializer source text, or {@code null} when the field has none. */
        String initializer;
    }

    /** One entry of {@code types}. */
    static final class Type {
        String fqn = "";
        String kind = "class";
        String file = "";
        String pkg = "";
        int line;
        final List<String> supertypes = new ArrayList<>();
        final List<String> typeParams = new ArrayList<>();
        final List<Method> ctors = new ArrayList<>();
        final List<Method> factories = new ArrayList<>();
        final List<Method> builders = new ArrayList<>();
        final List<Field> singletons = new ArrayList<>();
        /** Mixed list of {@link Method} and {@link Field} (public final fields), see brief §4. */
        final List<Object> observables = new ArrayList<>();
        final List<Method> methods = new ArrayList<>();
        final List<Field> fields = new ArrayList<>();
    }

    /** One entry of {@code tests[].helpers}. */
    static final class HelperRef {
        final String id;
        final String returns;

        HelperRef(String id, String returns) {
            this.id = id;
            this.returns = returns;
        }
    }

    /** One entry of {@code tests}. */
    static final class Test {
        String id = "";
        String file = "";
        String cls = "";
        String method = "";
        int lineStart;
        int lineEnd;
        String framework;
        String assertionLib;
        String mocking = "none";
        final List<Field> fixtures = new ArrayList<>();
        final List<String> callees = new ArrayList<>();
        final List<HelperRef> helpers = new ArrayList<>();
        String source = "";
    }

    /** The {@code project} block. */
    static final class Project {
        String name = "";
        String commit;
        String buildTool = "unknown";
        String javaVersion;
        final List<String> srcRoots = new ArrayList<>();
        final List<String> testRoots = new ArrayList<>();
        int nSourceFiles;
        int nTestFiles;
        String testFramework;
        String assertionLib;
        String mockingLib = "none";
    }

    /** Root object: {@code project}, {@code types}, {@code tests}, {@code test_helpers}. */
    static final class Index {
        final Project project = new Project();
        final List<Type> types = new ArrayList<>();
        final List<Test> tests = new ArrayList<>();
        /** Test-source type FQN to its non-void, non-private declared methods. */
        final Map<String, List<Method>> testHelpers = new LinkedHashMap<>();
    }
}
