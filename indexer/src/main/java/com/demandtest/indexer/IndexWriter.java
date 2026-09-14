package com.demandtest.indexer;

import com.google.gson.stream.JsonWriter;
import java.io.IOException;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

/**
 * Streaming {@code index.json} writer (SPEC §4.3, brief §6): a Gson {@link JsonWriter} with a
 * two-space indent, top-level order {@code project}, {@code types}, {@code tests},
 * {@code test_helpers}, parent directories created, stable order and no timestamps.
 *
 * <p>The document is never assembled as a tree: every entity is written as soon as it is visited,
 * so the memory footprint stays proportional to the model, not to the JSON text.
 *
 * <p>Field-shaped {@code observables} (public final fields, brief §4) are written in the Method
 * shape as well — {@code returns}, {@code params: []}, {@code visibility} — because
 * {@code demandtest/index.py} parses every {@code observables} entry as a {@code Method}; the Field
 * keys ({@code type}, {@code initializer}) are kept alongside so nothing is lost.
 */
final class IndexWriter {

    private IndexWriter() {}

    static void write(Path out, Model.Index index) throws IOException {
        Path parent = out.toAbsolutePath().getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }
        try (Writer writer = Files.newBufferedWriter(out, StandardCharsets.UTF_8);
             JsonWriter json = new JsonWriter(writer)) {
            json.setIndent("  ");
            json.setSerializeNulls(true);
            json.beginObject();
            json.name("project");
            writeProject(json, index.project);
            json.name("types");
            json.beginObject();
            for (Model.Type type : index.types) {
                json.name(type.fqn);
                writeType(json, type);
            }
            json.endObject();
            json.name("tests");
            json.beginArray();
            for (Model.Test test : index.tests) {
                writeTest(json, test);
            }
            json.endArray();
            json.name("test_helpers");
            json.beginObject();
            for (Map.Entry<String, List<Model.Method>> entry : index.testHelpers.entrySet()) {
                json.name(entry.getKey());
                json.beginArray();
                for (Model.Method method : entry.getValue()) {
                    writeMethod(json, method);
                }
                json.endArray();
            }
            json.endObject();
            json.endObject();
            json.flush();
            writer.write('\n');
            writer.flush();
        }
    }

    private static void writeProject(JsonWriter json, Model.Project project) throws IOException {
        json.beginObject();
        json.name("name").value(project.name);
        json.name("commit");
        writeNullable(json, project.commit);
        json.name("build_tool").value(project.buildTool);
        json.name("java_version");
        writeNullable(json, project.javaVersion);
        json.name("src_roots");
        writeStrings(json, project.srcRoots);
        json.name("test_roots");
        writeStrings(json, project.testRoots);
        json.name("n_source_files").value(project.nSourceFiles);
        json.name("n_test_files").value(project.nTestFiles);
        json.name("test_framework");
        writeNullable(json, project.testFramework);
        json.name("assertion_lib");
        writeNullable(json, project.assertionLib);
        json.name("mocking_lib").value(project.mockingLib);
        json.endObject();
    }

    private static void writeType(JsonWriter json, Model.Type type) throws IOException {
        json.beginObject();
        json.name("kind").value(type.kind);
        json.name("file").value(type.file);
        json.name("package").value(type.pkg);
        json.name("line").value(type.line);
        json.name("supertypes");
        writeStrings(json, type.supertypes);
        json.name("type_params");
        writeStrings(json, type.typeParams);
        json.name("ctors");
        writeMethods(json, type.ctors);
        json.name("factories");
        writeMethods(json, type.factories);
        json.name("builders");
        writeMethods(json, type.builders);
        json.name("singletons");
        json.beginArray();
        for (Model.Field field : type.singletons) {
            writeField(json, field);
        }
        json.endArray();
        json.name("observables");
        json.beginArray();
        for (Object observable : type.observables) {
            if (observable instanceof Model.Method method) {
                writeMethod(json, method);
            } else {
                writeObservableField(json, (Model.Field) observable);
            }
        }
        json.endArray();
        json.name("methods");
        writeMethods(json, type.methods);
        json.name("fields");
        json.beginArray();
        for (Model.Field field : type.fields) {
            writeField(json, field);
        }
        json.endArray();
        json.endObject();
    }

    private static void writeTest(JsonWriter json, Model.Test test) throws IOException {
        json.beginObject();
        json.name("id").value(test.id);
        json.name("file").value(test.file);
        json.name("class").value(test.cls);
        json.name("method").value(test.method);
        json.name("line_start").value(test.lineStart);
        json.name("line_end").value(test.lineEnd);
        json.name("framework");
        writeNullable(json, test.framework);
        json.name("assertion_lib");
        writeNullable(json, test.assertionLib);
        json.name("mocking").value(test.mocking);
        json.name("fixtures");
        json.beginArray();
        for (Model.Field fixture : test.fixtures) {
            writeField(json, fixture);
        }
        json.endArray();
        json.name("callees");
        writeStrings(json, test.callees);
        json.name("helpers");
        json.beginArray();
        for (Model.HelperRef helper : test.helpers) {
            json.beginObject();
            json.name("id").value(helper.id);
            json.name("returns").value(helper.returns);
            json.endObject();
        }
        json.endArray();
        json.name("source").value(test.source);
        json.endObject();
    }

    private static void writeMethods(JsonWriter json, List<Model.Method> methods) throws IOException {
        json.beginArray();
        for (Model.Method method : methods) {
            writeMethod(json, method);
        }
        json.endArray();
    }

    private static void writeMethod(JsonWriter json, Model.Method method) throws IOException {
        json.beginObject();
        json.name("name").value(method.name);
        json.name("params");
        json.beginArray();
        for (Model.Param param : method.params) {
            json.beginObject();
            json.name("name").value(param.name);
            json.name("type").value(param.type);
            json.name("resolved").value(param.resolved);
            json.endObject();
        }
        json.endArray();
        json.name("returns").value(method.returns);
        json.name("throws");
        writeStrings(json, method.thrown);
        json.name("static").value(method.isStatic);
        json.name("visibility").value(method.visibility);
        json.name("file").value(method.file);
        json.name("line").value(method.line);
        json.name("line_end").value(method.lineEnd);
        json.name("body_reads_fields");
        writeStrings(json, method.bodyReadsFields);
        json.name("body_writes_fields");
        writeStrings(json, method.bodyWritesFields);
        json.endObject();
    }

    private static void writeField(JsonWriter json, Model.Field field) throws IOException {
        json.beginObject();
        json.name("name").value(field.name);
        json.name("type").value(field.type);
        json.name("static").value(field.isStatic);
        json.name("visibility").value(field.visibility);
        json.name("file").value(field.file);
        json.name("line").value(field.line);
        json.name("initializer");
        writeNullable(json, field.initializer);
        json.endObject();
    }

    /** A public final field inside {@code observables}: Method shape plus the Field keys. */
    private static void writeObservableField(JsonWriter json, Model.Field field) throws IOException {
        json.beginObject();
        json.name("name").value(field.name);
        json.name("type").value(field.type);
        json.name("returns").value(field.type);
        json.name("params").beginArray().endArray();
        json.name("static").value(field.isStatic);
        json.name("visibility").value(field.visibility);
        json.name("file").value(field.file);
        json.name("line").value(field.line);
        json.name("initializer");
        writeNullable(json, field.initializer);
        json.endObject();
    }

    private static void writeStrings(JsonWriter json, List<String> values) throws IOException {
        json.beginArray();
        for (String value : values) {
            json.value(value);
        }
        json.endArray();
    }

    private static void writeNullable(JsonWriter json, String value) throws IOException {
        if (value == null) {
            json.nullValue();
        } else {
            json.value(value);
        }
    }
}
