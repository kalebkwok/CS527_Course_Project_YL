package com.demandtest.indexer;

import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.ImportDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.AnnotationExpr;
import java.util.Set;

/**
 * Test-framework / assertion-library / mocking detection (brief §3). Detection is textual (imports
 * and annotations), never symbolic: JUnit and Mockito are usually absent from the repository
 * classpath and resolving them would turn a cheap check into a solver miss.
 */
final class Idioms {

    /** Method annotations that make a method a test (brief §5). */
    private static final Set<String> TEST_ANNOTATIONS =
            Set.of("Test", "ParameterizedTest", "RepeatedTest", "TestFactory");

    boolean jupiterImports;
    boolean junit4Imports;
    boolean assertjImports;
    boolean truthImports;
    boolean hamcrestImports;
    boolean junitAssertImports;
    boolean mockitoImports;
    boolean usesTestAnnotation;

    static Idioms of(CompilationUnit unit) {
        Idioms idioms = new Idioms();
        for (ImportDeclaration declaration : unit.getImports()) {
            idioms.observeName(declaration.getNameAsString());
        }
        for (AnnotationExpr annotation : unit.findAll(AnnotationExpr.class)) {
            idioms.observeName(annotation.getNameAsString());
        }
        for (MethodDeclaration method : unit.findAll(MethodDeclaration.class)) {
            if (isTestAnnotated(method)) {
                idioms.usesTestAnnotation = true;
            }
        }
        return idioms;
    }

    void merge(Idioms other) {
        jupiterImports |= other.jupiterImports;
        junit4Imports |= other.junit4Imports;
        assertjImports |= other.assertjImports;
        truthImports |= other.truthImports;
        hamcrestImports |= other.hamcrestImports;
        junitAssertImports |= other.junitAssertImports;
        mockitoImports |= other.mockitoImports;
        usesTestAnnotation |= other.usesTestAnnotation;
    }

    private void observeName(String name) {
        if (name == null) {
            return;
        }
        boolean jupiter = name.startsWith("org.junit.jupiter");
        if (jupiter) {
            jupiterImports = true;
        } else if (name.startsWith("org.junit.")) {
            junit4Imports = true;
        }
        if (name.startsWith("org.junit.jupiter.api.Assertions") || name.startsWith("org.junit.Assert")) {
            junitAssertImports = true;
        }
        if (name.startsWith("org.assertj.core.api")) {
            assertjImports = true;
        }
        if (name.startsWith("com.google.common.truth")) {
            truthImports = true;
        }
        if (name.startsWith("org.hamcrest")) {
            hamcrestImports = true;
        }
        if (name.startsWith("org.mockito")) {
            mockitoImports = true;
        }
    }

    static boolean isTestAnnotated(MethodDeclaration method) {
        for (AnnotationExpr annotation : method.getAnnotations()) {
            if (TEST_ANNOTATIONS.contains(annotation.getName().getIdentifier())) {
                return true;
            }
        }
        return false;
    }

    String framework(boolean pomJupiter, boolean pomJunit4) {
        if (jupiterImports) {
            return "junit5";
        }
        if (junit4Imports) {
            return "junit4";
        }
        if (usesTestAnnotation) {
            return (!pomJupiter && pomJunit4) ? "junit4" : "junit5";
        }
        if (pomJupiter) {
            return "junit5";
        }
        if (pomJunit4) {
            return "junit4";
        }
        return null;
    }

    /** Priority assertj &gt; truth &gt; hamcrest &gt; junit (brief §3). */
    String assertionLib() {
        if (assertjImports) {
            return "assertj";
        }
        if (truthImports) {
            return "truth";
        }
        if (hamcrestImports) {
            return "hamcrest";
        }
        if (junitAssertImports) {
            return "junit";
        }
        return null;
    }

    String mocking(boolean pomMockito) {
        return (mockitoImports || pomMockito) ? "mockito" : "none";
    }
}
