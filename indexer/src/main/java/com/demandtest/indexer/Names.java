package com.demandtest.indexer;

import com.github.javaparser.ast.type.Type;
import com.github.javaparser.resolution.declarations.ResolvedReferenceTypeDeclaration;
import com.github.javaparser.resolution.types.ResolvedType;
import java.util.Optional;

/**
 * Type-name helpers: JavaParser {@link ResolvedType} to the index's string form, plus the two
 * erasure flavours the schema needs.
 *
 * <ul>
 *   <li>{@link #eraseGenerics(String)} keeps array brackets ({@code Map<String, Integer>} becomes
 *       {@code java.util.Map}, {@code String[]} stays {@code java.lang.String[]}) — used for
 *       {@code params[].type}, {@code returns}, field types and supertypes (IMPLEMENTATION_BRIEF §4).
 *   <li>{@link #signatureErase(String)} additionally strips array brackets and varargs, mirroring
 *       {@code demandtest/index.py:erase} exactly. {@code callees} and {@code helpers[].id} must use
 *       it: {@code demandtest/packet.py} builds the lookup key as
 *       {@code f"{C.fqn}#{m.erased_sig()}"} with the Python erase, so a bracket difference would make
 *       every array-parameter callee invisible to {@code Index.tests_calling}.
 * </ul>
 */
final class Names {

    private Names() {}

    /** Removes angle-bracketed type arguments, keeping everything else (brackets included). */
    static String eraseGenerics(String text) {
        if (text == null) {
            return null;
        }
        StringBuilder out = new StringBuilder(text.length());
        int depth = 0;
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (c == '<') {
                depth++;
            } else if (c == '>') {
                depth = Math.max(0, depth - 1);
            } else if (depth == 0) {
                out.append(c);
            }
        }
        return out.toString().trim();
    }

    /** {@code demandtest/index.py:erase}: generics, {@code ...} and {@code []} all disappear. */
    static String signatureErase(String text) {
        if (text == null) {
            return null;
        }
        String t = text.trim().replace("...", "[]");
        StringBuilder out = new StringBuilder(t.length());
        int depth = 0;
        for (int i = 0; i < t.length(); i++) {
            char c = t.charAt(i);
            if (c == '<') {
                depth++;
            } else if (c == '>') {
                depth = Math.max(0, depth - 1);
            } else if (depth == 0) {
                out.append(c);
            }
        }
        return out.toString().replace("[]", "").trim();
    }

    static String simpleName(String type) {
        String erased = eraseGenerics(type);
        if (erased == null || erased.isEmpty()) {
            return erased;
        }
        int dot = erased.lastIndexOf('.');
        return dot < 0 ? erased : erased.substring(dot + 1);
    }

    /** Resolved, erased FQN of an AST type; empty when the symbol solver cannot resolve it. */
    static Optional<String> resolveName(Type type) {
        try {
            return Optional.ofNullable(resolvedName(type.resolve()));
        } catch (Throwable failure) {
            return Optional.empty();
        }
    }

    /** Resolved, erased FQN (type arguments erased, array brackets kept) or {@code null}. */
    static String resolvedName(ResolvedType type) {
        if (type == null) {
            return null;
        }
        try {
            if (type.isArray()) {
                String component = resolvedName(type.asArrayType().getComponentType());
                return component == null ? null : component + "[]";
            }
            if (type.isPrimitive()) {
                return type.asPrimitive().describe();
            }
            if (type.isVoid()) {
                return "void";
            }
            if (type.isTypeVariable()) {
                return type.asTypeVariable().describe();
            }
            if (type.isReferenceType()) {
                Optional<ResolvedReferenceTypeDeclaration> declaration = type.asReferenceType().getTypeDeclaration();
                if (declaration.isPresent()) {
                    return declaration.get().getQualifiedName();
                }
                return eraseGenerics(type.describe());
            }
            return eraseGenerics(type.describe());
        } catch (Throwable failure) {
            return eraseGenerics(describeQuietly(type));
        }
    }

    private static String describeQuietly(ResolvedType type) {
        try {
            return type.describe();
        } catch (Throwable failure) {
            return "?";
        }
    }
}
