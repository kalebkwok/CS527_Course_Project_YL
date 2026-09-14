package com.demandtest.indexer;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ParseResult;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.NodeList;
import com.github.javaparser.ast.body.AnnotationDeclaration;
import com.github.javaparser.ast.body.BodyDeclaration;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.CompactConstructorDeclaration;
import com.github.javaparser.ast.body.ConstructorDeclaration;
import com.github.javaparser.ast.body.EnumConstantDeclaration;
import com.github.javaparser.ast.body.EnumDeclaration;
import com.github.javaparser.ast.body.FieldDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.Parameter;
import com.github.javaparser.ast.body.RecordDeclaration;
import com.github.javaparser.ast.body.TypeDeclaration;
import com.github.javaparser.ast.body.VariableDeclarator;
import com.github.javaparser.ast.expr.AssignExpr;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.FieldAccessExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.expr.ObjectCreationExpr;
import com.github.javaparser.ast.expr.UnaryExpr;
import com.github.javaparser.ast.expr.VariableDeclarationExpr;
import com.github.javaparser.ast.stmt.BlockStmt;
import com.github.javaparser.ast.type.ClassOrInterfaceType;
import com.github.javaparser.ast.type.ReferenceType;
import com.github.javaparser.ast.type.Type;
import com.github.javaparser.ast.type.TypeParameter;
import com.github.javaparser.resolution.declarations.ResolvedMethodDeclaration;
import com.github.javaparser.resolution.declarations.ResolvedReferenceTypeDeclaration;
import com.github.javaparser.resolution.types.ResolvedReferenceType;
import com.github.javaparser.resolution.types.ResolvedType;
import java.util.ArrayList;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/**
 * Turns parsed compilation units into the {@link Model} (SPEC §4.2).
 *
 * <p>Two passes, because a test's {@code callees} must be classified against the set of main-source
 * types: every file under a source root is indexed first, then every test file. Field reads/writes
 * are flow-insensitive name matches against the fields declared in the owning type, with parameters
 * and local variables shadowing them (brief §4).
 */
final class Extractor {

    private final ProblemLog log;
    private final boolean pomJupiter;
    private final boolean pomJunit4;
    private final boolean pomMockito;

    private final Map<String, Model.Type> mainTypes = new LinkedHashMap<>();
    private final Map<String, Model.Type> testTypes = new LinkedHashMap<>();
    private final Set<String> mainSimpleNames = new LinkedHashSet<>();
    private final Idioms projectIdioms = new Idioms();

    Extractor(Pom pom, ProblemLog log) {
        this.log = log;
        this.pomJupiter = pom != null && pom.junitJupiter;
        this.pomJunit4 = pom != null && pom.junit4;
        this.pomMockito = pom != null && pom.mockito;
    }

    void extract(List<RepoLayout.JavaFile> files, JavaParser parser, Model.Index index) {
        // Pass 1: every main-source type, so a test call can be classified as a callee.
        for (RepoLayout.JavaFile file : files) {
            if (!file.testRoot()) {
                CompilationUnit unit = parse(file, parser);
                if (unit != null) {
                    collectTypes(unit, file, index, null);
                }
            }
        }
        // Pass 2: every test-source type, so a test call can be classified as a helper even when the
        // helper lives in a file that sorts after the test (callees are resolved against testTypes).
        List<TestUnit> testUnits = new ArrayList<>();
        for (RepoLayout.JavaFile file : files) {
            if (!file.testRoot()) {
                continue;
            }
            CompilationUnit unit = parse(file, parser);
            if (unit == null) {
                continue;
            }
            Idioms idioms = Idioms.of(unit);
            projectIdioms.merge(idioms);
            collectTypes(unit, file, index, idioms);
            testUnits.add(new TestUnit(file, unit, idioms));
        }
        // Pass 3: the test methods themselves.
        for (TestUnit testUnit : testUnits) {
            collectTests(testUnit, index);
        }
        index.types.addAll(mainTypes.values());
        index.project.testFramework = projectIdioms.framework(pomJupiter, pomJunit4);
        index.project.assertionLib = projectIdioms.assertionLib();
        index.project.mockingLib = projectIdioms.mocking(pomMockito);
    }

    // ------------------------------------------------------------------ parsing

    /** Parses one file; {@code null} (with a warning) when it cannot be parsed at all. */
    private CompilationUnit parse(RepoLayout.JavaFile file, JavaParser parser) {
        ParseResult<CompilationUnit> result;
        try {
            result = parser.parse(file.path());
        } catch (Throwable failure) {
            log.warn("unparsable file, skipped: " + file.relPath() + " (" + failure + ")");
            return null;
        }
        if (result.getResult().isEmpty()) {
            log.warn("unparsable file, skipped: " + file.relPath() + " " + result.getProblems());
            return null;
        }
        if (!result.getProblems().isEmpty()) {
            log.warn("recovered parse problems in " + file.relPath() + ": " + result.getProblems());
        }
        return result.getResult().get();
    }

    private void collectTypes(CompilationUnit unit, RepoLayout.JavaFile file, Model.Index index, Idioms idioms) {
        try {
            String pkg = unit.getPackageDeclaration().map(declaration -> declaration.getNameAsString()).orElse("");
            for (TypeDeclaration<?> type : unit.getTypes()) {
                collectType(type, pkg, null, file, index, idioms);
            }
        } catch (Throwable failure) {
            log.warn("failed to index " + file.relPath() + " (" + failure + ")");
        }
    }

    private void collectType(TypeDeclaration<?> declaration, String pkg, String enclosing,
                             RepoLayout.JavaFile file, Model.Index index, Idioms idioms) {
        String fqn = enclosing == null
                ? (pkg.isEmpty() ? declaration.getNameAsString() : pkg + "." + declaration.getNameAsString())
                : enclosing + "." + declaration.getNameAsString();
        Model.Type type = buildType(declaration, fqn, pkg, file);
        if (file.testRoot()) {
            testTypes.put(fqn, type);
            List<Model.Method> helpers = new ArrayList<>();
            for (Model.Method method : type.methods) {
                if (!method.returns.equals("void") && !method.visibility.equals("private")) {
                    helpers.add(method);
                }
            }
            if (!helpers.isEmpty()) {
                index.testHelpers.put(fqn, helpers);
            }
        } else {
            mainTypes.put(fqn, type);
            mainSimpleNames.add(Names.simpleName(fqn));
        }
        for (BodyDeclaration<?> member : declaration.getMembers()) {
            if (member instanceof TypeDeclaration<?> nested) {
                collectType(nested, pkg, fqn, file, index, idioms);
            }
        }
    }

    // --------------------------------------------------------------------- types

    private Model.Type buildType(TypeDeclaration<?> declaration, String fqn, String pkg, RepoLayout.JavaFile file) {
        Model.Type type = new Model.Type();
        type.fqn = fqn;
        type.kind = kindOf(declaration);
        type.file = file.relPath();
        type.pkg = pkg;
        type.line = beginLine(declaration);
        for (TypeParameter parameter : typeParameters(declaration)) {
            type.typeParams.add(parameter.getNameAsString());
        }
        for (ClassOrInterfaceType supertype : supertypeNodes(declaration)) {
            String name = typeNameOrText(supertype);
            type.supertypes.add(name);
        }
        type.fields.addAll(fieldsOf(declaration, type));

        List<MethodBuild> builds = methodBuilds(declaration, type);
        for (MethodBuild build : builds) {
            type.methods.add(build.model);
        }
        for (MethodBuild build : builds) {
            Model.Method method = build.model;
            if (method.isStatic && isSubtypeOrSelf(build.returnType, type.fqn)) {
                type.factories.add(method);
            }
            if (returnsBuilderOf(build, type.fqn)) {
                type.builders.add(method);
            }
            if (isObservableMethod(method)) {
                type.observables.add(method);
            }
        }
        for (Model.Field field : type.fields) {
            if (field.isFinal && field.visibility.equals("public")) {
                type.observables.add(field);
            }
        }
        type.singletons.addAll(singletonsOf(declaration, type));
        type.ctors.addAll(ctorsOf(declaration, type));
        return type;
    }

    private static String kindOf(TypeDeclaration<?> declaration) {
        if (declaration instanceof AnnotationDeclaration) {
            return "interface";
        }
        if (declaration instanceof EnumDeclaration) {
            return "enum";
        }
        if (declaration instanceof RecordDeclaration) {
            return "record";
        }
        if (declaration instanceof ClassOrInterfaceDeclaration classOrInterface) {
            if (classOrInterface.isInterface()) {
                return "interface";
            }
            return classOrInterface.isAbstract() ? "abstract" : "class";
        }
        return "class";
    }

    private static boolean isInterfaceLike(TypeDeclaration<?> declaration) {
        return (declaration instanceof ClassOrInterfaceDeclaration classOrInterface && classOrInterface.isInterface())
                || declaration instanceof AnnotationDeclaration;
    }

    private static boolean isPublicType(TypeDeclaration<?> declaration) {
        if (declaration instanceof ClassOrInterfaceDeclaration classOrInterface) {
            return classOrInterface.isPublic();
        }
        if (declaration instanceof EnumDeclaration enumeration) {
            return enumeration.isPublic();
        }
        if (declaration instanceof RecordDeclaration record) {
            return record.isPublic();
        }
        if (declaration instanceof AnnotationDeclaration annotation) {
            return annotation.isPublic();
        }
        return false;
    }

    private static List<ClassOrInterfaceType> supertypeNodes(TypeDeclaration<?> declaration) {
        List<ClassOrInterfaceType> out = new ArrayList<>();
        if (declaration instanceof ClassOrInterfaceDeclaration classOrInterface) {
            out.addAll(classOrInterface.getExtendedTypes());
            out.addAll(classOrInterface.getImplementedTypes());
        } else if (declaration instanceof EnumDeclaration enumeration) {
            out.addAll(enumeration.getImplementedTypes());
        } else if (declaration instanceof RecordDeclaration record) {
            out.addAll(record.getImplementedTypes());
        }
        return out;
    }

    private static NodeList<TypeParameter> typeParameters(TypeDeclaration<?> declaration) {
        if (declaration instanceof ClassOrInterfaceDeclaration classOrInterface) {
            return classOrInterface.getTypeParameters();
        }
        if (declaration instanceof RecordDeclaration record) {
            return record.getTypeParameters();
        }
        return new NodeList<>();
    }

    private List<Model.Field> fieldsOf(TypeDeclaration<?> declaration, Model.Type type) {
        List<Model.Field> out = new ArrayList<>();
        boolean interfaceLike = isInterfaceLike(declaration);
        for (BodyDeclaration<?> member : declaration.getMembers()) {
            if (!(member instanceof FieldDeclaration fieldDeclaration)) {
                continue;
            }
            for (VariableDeclarator variable : fieldDeclaration.getVariables()) {
                Model.Field field = new Model.Field();
                field.name = variable.getNameAsString();
                field.type = typeNameOrText(variable.getType());
                field.isStatic = fieldDeclaration.isStatic() || interfaceLike;
                field.isFinal = fieldDeclaration.isFinal() || interfaceLike;
                field.visibility = visibility(fieldDeclaration.isPublic(), fieldDeclaration.isProtected(),
                        fieldDeclaration.isPrivate(), interfaceLike);
                field.file = type.file;
                field.line = beginLine(variable) > 0 ? beginLine(variable) : beginLine(fieldDeclaration);
                field.initializer = variable.getInitializer().map(Expression::toString).orElse(null);
                out.add(field);
            }
        }
        return out;
    }

    /** Enum constants plus static fields whose erased type is the owner (brief §4). */
    private List<Model.Field> singletonsOf(TypeDeclaration<?> declaration, Model.Type type) {
        List<Model.Field> out = new ArrayList<>();
        if (declaration instanceof EnumDeclaration enumeration) {
            for (EnumConstantDeclaration constant : enumeration.getEntries()) {
                Model.Field field = new Model.Field();
                field.name = constant.getNameAsString();
                field.type = type.fqn;
                field.isStatic = true;
                field.isFinal = true;
                field.visibility = "public";
                field.file = type.file;
                field.line = beginLine(constant);
                field.initializer = null;
                out.add(field);
            }
        }
        for (Model.Field field : type.fields) {
            if (field.isStatic && field.type.equals(type.fqn)) {
                out.add(field);
            }
        }
        return out;
    }

    private List<Model.Method> ctorsOf(TypeDeclaration<?> declaration, Model.Type type) {
        List<Model.Method> out = new ArrayList<>();
        boolean record = declaration instanceof RecordDeclaration;
        RecordDeclaration recordDeclaration = record ? (RecordDeclaration) declaration : null;
        boolean anyDeclared = false;
        boolean canonicalSeen = false;
        for (BodyDeclaration<?> member : declaration.getMembers()) {
            if (member instanceof ConstructorDeclaration ctor) {
                anyDeclared = true;
                if (record && sameTypes(ctor.getParameters(), recordDeclaration.getParameters())) {
                    canonicalSeen = true;
                }
                if (ctor.isPrivate()) {
                    continue; // private constructors are never indexed (SPEC §4.2)
                }
                out.add(ctorModel(ctor, type));
            } else if (member instanceof CompactConstructorDeclaration compact) {
                anyDeclared = true;
                canonicalSeen = true;
                if (compact.isPrivate()) {
                    continue;
                }
                out.add(ctorModel(compact, recordDeclaration, type));
            }
        }
        if (record && !canonicalSeen) {
            out.add(synthesizedCtor(recordDeclaration, type));
        } else if (!anyDeclared && declaration instanceof ClassOrInterfaceDeclaration classOrInterface
                && !classOrInterface.isInterface()) {
            out.add(synthesizedDefaultCtor(declaration, type));
        }
        return out;
    }

    private Model.Method ctorModel(ConstructorDeclaration ctor, Model.Type type) {
        Model.Method method = baseCtor(ctor, type);
        for (Parameter parameter : ctor.getParameters()) {
            method.params.add(param(parameter));
        }
        for (ReferenceType exception : ctor.getThrownExceptions()) {
            method.thrown.add(typeNameOrText(exception));
        }
        analyzeBody(ctor.getBody(), parameterNames(ctor.getParameters()), type, method);
        return method;
    }

    private Model.Method ctorModel(CompactConstructorDeclaration compact, RecordDeclaration record, Model.Type type) {
        Model.Method method = baseCtor(compact, type);
        NodeList<Parameter> parameters = record == null ? new NodeList<>() : record.getParameters();
        for (Parameter parameter : parameters) {
            method.params.add(param(parameter));
        }
        for (ReferenceType exception : compact.getThrownExceptions()) {
            method.thrown.add(typeNameOrText(exception));
        }
        analyzeBody(compact.getBody(), parameterNames(parameters), type, method);
        return method;
    }

    private static Model.Method baseCtor(BodyDeclaration<?> node, Model.Type type) {
        Model.Method method = new Model.Method();
        method.name = "<init>";
        method.returns = "void";
        boolean isPublic = false;
        boolean isProtected = false;
        boolean isPrivate = false;
        if (node instanceof ConstructorDeclaration ctor) {
            isPublic = ctor.isPublic();
            isProtected = ctor.isProtected();
            isPrivate = ctor.isPrivate();
        } else if (node instanceof CompactConstructorDeclaration compact) {
            isPublic = compact.isPublic();
            isProtected = compact.isProtected();
            isPrivate = compact.isPrivate();
        }
        method.visibility = visibility(isPublic, isProtected, isPrivate, false);
        method.file = type.file;
        method.line = beginLine(node);
        method.lineEnd = endLine(node);
        return method;
    }

    /** Implicit default constructor: public when the type is public, package-private otherwise. */
    private static Model.Method synthesizedDefaultCtor(TypeDeclaration<?> declaration, Model.Type type) {
        Model.Method ctor = new Model.Method();
        ctor.name = "<init>";
        ctor.returns = "void";
        ctor.visibility = isPublicType(declaration) ? "public" : "package";
        ctor.file = type.file;
        ctor.line = type.line;
        ctor.lineEnd = type.line;
        return ctor;
    }

    /** Record canonical constructor, synthesized when the record declares no matching one. */
    private Model.Method synthesizedCtor(RecordDeclaration record, Model.Type type) {
        Model.Method ctor = new Model.Method();
        ctor.name = "<init>";
        ctor.returns = "void";
        ctor.visibility = isPublicType(record) ? "public" : "package";
        ctor.file = type.file;
        ctor.line = type.line;
        ctor.lineEnd = type.line;
        for (Parameter parameter : record.getParameters()) {
            ctor.params.add(param(parameter));
        }
        return ctor;
    }

    private List<MethodBuild> methodBuilds(TypeDeclaration<?> declaration, Model.Type type) {
        List<MethodBuild> out = new ArrayList<>();
        boolean interfaceLike = isInterfaceLike(declaration);
        for (BodyDeclaration<?> member : declaration.getMembers()) {
            if (!(member instanceof MethodDeclaration method)) {
                continue;
            }
            Model.Method model = new Model.Method();
            model.name = method.getNameAsString();
            for (Parameter parameter : method.getParameters()) {
                model.params.add(param(parameter));
            }
            ResolvedType returnType = null;
            if (method.getType().isVoidType()) {
                model.returns = "void";
            } else {
                returnType = resolveQuietly(method.getType());
                String name = returnType == null ? null : Names.resolvedName(returnType);
                if (name == null) {
                    log.unresolved(method.getType().asString());
                    name = Names.eraseGenerics(method.getType().asString());
                }
                model.returns = name;
            }
            for (ReferenceType exception : method.getThrownExceptions()) {
                model.thrown.add(typeNameOrText(exception));
            }
            model.isStatic = method.isStatic();
            model.visibility = visibility(method.isPublic(), method.isProtected(), method.isPrivate(), interfaceLike);
            model.file = type.file;
            model.line = beginLine(method);
            model.lineEnd = endLine(method);
            method.getBody().ifPresent(body -> analyzeBody(body, parameterNames(method.getParameters()), type, model));
            out.add(new MethodBuild(model, returnType, referenceDeclaration(returnType)));
        }
        return out;
    }

    private Model.Param param(Parameter parameter) {
        ResolvedType resolved = resolveQuietly(parameter.getType());
        String name = resolved == null ? null : Names.resolvedName(resolved);
        boolean isResolved = name != null;
        if (!isResolved) {
            log.unresolved(parameter.getType().asString());
            name = Names.eraseGenerics(parameter.getType().asString());
        }
        if (parameter.isVarArgs()) {
            name = name + "[]";
        }
        return new Model.Param(parameter.getNameAsString(), name, isResolved);
    }

    /** Resolved erased FQN, or the source text when the solver cannot resolve it (never dropped). */
    private String typeNameOrText(Type type) {
        ResolvedType resolved = resolveQuietly(type);
        String name = resolved == null ? null : Names.resolvedName(resolved);
        if (name == null) {
            log.unresolved(type.asString());
            name = Names.eraseGenerics(type.asString());
        }
        return name;
    }

    private static List<String> parameterNames(NodeList<Parameter> parameters) {
        List<String> out = new ArrayList<>();
        for (Parameter parameter : parameters) {
            out.add(parameter.getNameAsString());
        }
        return out;
    }

    // --------------------------------------------------------- classification

    /** {@code static} method whose return type erases to the owner or a subtype of it (brief §4). */
    private boolean isSubtypeOrSelf(ResolvedType returnType, String ownerFqn) {
        if (returnType == null || !returnType.isReferenceType()) {
            return false;
        }
        try {
            Optional<ResolvedReferenceTypeDeclaration> declaration =
                    returnType.asReferenceType().getTypeDeclaration();
            if (declaration.isEmpty()) {
                return false;
            }
            if (ownerFqn.equals(declaration.get().getQualifiedName())) {
                return true;
            }
            for (ResolvedReferenceType ancestor : declaration.get().getAllAncestors()) {
                if (ancestor.getTypeDeclaration().map(d -> ownerFqn.equals(d.getQualifiedName())).orElse(false)) {
                    return true;
                }
            }
        } catch (Throwable failure) {
            return false;
        }
        return false;
    }

    /** Method returning a {@code *Builder} type whose declared {@code build()} returns the owner. */
    private boolean returnsBuilderOf(MethodBuild build, String ownerFqn) {
        String simple = Names.simpleName(build.model.returns);
        if (simple == null || !simple.endsWith("Builder") || build.returnDeclaration == null) {
            return false;
        }
        try {
            for (ResolvedMethodDeclaration method : build.returnDeclaration.getDeclaredMethods()) {
                if (method.getName().equals("build") && method.getNumberOfParams() == 0
                        && ownerFqn.equals(Names.resolvedName(method.getReturnType()))) {
                    return true;
                }
            }
        } catch (Throwable failure) {
            return false;
        }
        return false;
    }

    private static boolean isObservableMethod(Model.Method method) {
        boolean visible = !method.visibility.equals("private");
        if (method.visibility.equals("public") && method.params.isEmpty() && !method.returns.equals("void")) {
            return true; // (a) public, no parameter, non-void
        }
        if (visible && (method.name.equals("equals") || method.name.equals("hashCode")
                || method.name.equals("toString"))) {
            return true; // (b)
        }
        return visible && (method.name.equals("size") || method.name.equals("isEmpty")
                || method.name.equals("contains")); // (c)
    }

    // ------------------------------------------------------------ body analysis

    private void analyzeBody(BlockStmt body, List<String> parameterNames, Model.Type owner, Model.Method method) {
        Map<String, Integer> fieldOrder = new LinkedHashMap<>();
        for (Model.Field field : owner.fields) {
            fieldOrder.putIfAbsent(field.name, fieldOrder.size());
        }
        Set<String> locals = new LinkedHashSet<>(parameterNames);
        for (Parameter parameter : body.findAll(Parameter.class)) {
            locals.add(parameter.getNameAsString());
        }
        for (VariableDeclarator variable : body.findAll(VariableDeclarator.class)) {
            locals.add(variable.getNameAsString());
        }
        for (VariableDeclarationExpr declaration : body.findAll(VariableDeclarationExpr.class)) {
            declaration.getVariables().forEach(variable -> locals.add(variable.getNameAsString()));
        }

        Set<Expression> writeTargets = Collections.newSetFromMap(new IdentityHashMap<>());
        for (AssignExpr assignment : body.findAll(AssignExpr.class)) {
            writeTargets.add(assignment.getTarget());
        }
        for (UnaryExpr unary : body.findAll(UnaryExpr.class)) {
            writeTargets.add(unary.getExpression());
        }

        Set<String> reads = new LinkedHashSet<>();
        Set<String> writes = new LinkedHashSet<>();
        for (FieldAccessExpr access : body.findAll(FieldAccessExpr.class)) {
            if (access.getScope().isThisExpr() && fieldOrder.containsKey(access.getNameAsString())
                    && !writeTargets.contains(access)) {
                reads.add(access.getNameAsString());
            }
        }
        for (NameExpr name : body.findAll(NameExpr.class)) {
            String identifier = name.getNameAsString();
            if (fieldOrder.containsKey(identifier) && !locals.contains(identifier) && !writeTargets.contains(name)) {
                reads.add(identifier);
            }
        }
        for (AssignExpr assignment : body.findAll(AssignExpr.class)) {
            String field = fieldNameOf(assignment.getTarget(), fieldOrder, locals);
            if (field != null) {
                writes.add(field);
                if (assignment.getOperator() != AssignExpr.Operator.ASSIGN) {
                    reads.add(field); // compound assignment reads as well (brief §4)
                }
            }
        }
        for (UnaryExpr unary : body.findAll(UnaryExpr.class)) {
            String field = fieldNameOf(unary.getExpression(), fieldOrder, locals);
            if (field != null) {
                writes.add(field);
                reads.add(field);
            }
        }
        method.bodyReadsFields.addAll(inDeclarationOrder(reads, fieldOrder));
        method.bodyWritesFields.addAll(inDeclarationOrder(writes, fieldOrder));
    }

    private static String fieldNameOf(Expression expression, Map<String, Integer> fieldOrder, Set<String> locals) {
        if (expression instanceof NameExpr name) {
            String identifier = name.getNameAsString();
            return fieldOrder.containsKey(identifier) && !locals.contains(identifier) ? identifier : null;
        }
        if (expression instanceof FieldAccessExpr access
                && access.getScope().isThisExpr()
                && fieldOrder.containsKey(access.getNameAsString())) {
            return access.getNameAsString();
        }
        return null;
    }

    private static List<String> inDeclarationOrder(Set<String> names, Map<String, Integer> fieldOrder) {
        List<String> out = new ArrayList<>(names);
        out.sort((a, b) -> Integer.compare(fieldOrder.getOrDefault(a, Integer.MAX_VALUE),
                fieldOrder.getOrDefault(b, Integer.MAX_VALUE)));
        return out;
    }

    // -------------------------------------------------------------------- tests

    private void collectTests(TestUnit testUnit, Model.Index index) {
        try {
            String pkg = testUnit.unit.getPackageDeclaration()
                    .map(declaration -> declaration.getNameAsString()).orElse("");
            for (TypeDeclaration<?> declaration : testUnit.unit.getTypes()) {
                collectTests(declaration, pkg, null, testUnit, index);
            }
        } catch (Throwable failure) {
            log.warn("failed to index tests of " + testUnit.file.relPath() + " (" + failure + ")");
        }
    }

    private void collectTests(TypeDeclaration<?> declaration, String pkg, String enclosing,
                              TestUnit testUnit, Model.Index index) {
        String fqn = enclosing == null
                ? (pkg.isEmpty() ? declaration.getNameAsString() : pkg + "." + declaration.getNameAsString())
                : enclosing + "." + declaration.getNameAsString();
        Model.Type owner = testTypes.get(fqn);
        if (owner != null) {
            for (MethodDeclaration method : declaration.getMethods()) {
                if (Idioms.isTestAnnotated(method)) {
                    index.tests.add(buildTest(method, owner, testUnit.file, testUnit.idioms));
                }
            }
        }
        for (BodyDeclaration<?> member : declaration.getMembers()) {
            if (member instanceof TypeDeclaration<?> nested) {
                collectTests(nested, pkg, fqn, testUnit, index);
            }
        }
    }

    private Model.Test buildTest(MethodDeclaration method, Model.Type owner, RepoLayout.JavaFile file, Idioms idioms) {
        Model.Test test = new Model.Test();
        test.id = owner.fqn + "#" + method.getNameAsString();
        test.cls = owner.fqn;
        test.method = method.getNameAsString();
        test.file = file.relPath();
        test.lineStart = beginLine(method);
        test.lineEnd = endLine(method);
        if (idioms != null) {
            test.framework = idioms.framework(pomJupiter, pomJunit4);
            test.assertionLib = idioms.assertionLib();
            test.mocking = idioms.mocking(pomMockito);
        }
        for (Model.Field field : owner.fields) {
            if (!field.isStatic) {
                test.fixtures.add(field);
            }
        }
        test.source = method.toString();
        collectCalls(method, owner, test);
        return test;
    }

    private void collectCalls(MethodDeclaration method, Model.Type owner, Model.Test test) {
        if (method.getBody().isEmpty()) {
            return;
        }
        BlockStmt body = method.getBody().get();
        Map<String, String> localTypes = localTypeNames(body, owner);
        Set<String> calleeSignatures = new LinkedHashSet<>();
        Set<String> helperIds = new LinkedHashSet<>();
        for (MethodCallExpr call : body.findAll(MethodCallExpr.class)) {
            ResolvedMethodDeclaration resolved;
            String ownerFqn;
            String returns;
            try {
                resolved = call.resolve();
                ownerFqn = resolved.declaringType().getQualifiedName();
                returns = Names.resolvedName(resolved.getReturnType());
            } catch (Throwable failure) {
                resolved = null;
                ownerFqn = null;
                returns = null;
            }
            if (resolved != null && ownerFqn != null) {
                String canonical = canonicalOwner(ownerFqn);
                String signature = canonical + "#" + resolved.getName() + "(" + paramSignature(resolved) + ")";
                if (mainTypes.containsKey(canonical)) {
                    calleeSignatures.add(signature);
                } else if (testTypes.containsKey(canonical) && helperIds.add(signature)) {
                    test.helpers.add(new Model.HelperRef(signature, returns == null ? "void" : returns));
                }
                continue;
            }
            if (looksLikeProjectCall(call, localTypes)) {
                calleeSignatures.add("<unresolved>#" + call.getNameAsString()
                        + "(" + unresolvedParamSignature(call) + ")");
            }
        }
        test.callees.addAll(calleeSignatures);
    }

    /** A receiver that is (or could be) a main-source type, so the solver failure is recorded. */
    private boolean looksLikeProjectCall(MethodCallExpr call, Map<String, String> localTypes) {
        Optional<Expression> scope = call.getScope();
        if (scope.isEmpty()) {
            return false;
        }
        Expression expression = scope.get();
        try {
            String simple = Names.simpleName(Names.resolvedName(expression.calculateResolvedType()));
            if (simple != null && mainSimpleNames.contains(simple)) {
                return true;
            }
        } catch (Throwable ignored) {
            // unresolved receiver: fall through to the syntactic heuristics
        }
        if (expression instanceof NameExpr name) {
            String declared = localTypes.get(name.getNameAsString());
            if (declared != null && mainSimpleNames.contains(declared)) {
                return true;
            }
            return mainSimpleNames.contains(name.getNameAsString());
        }
        if (expression instanceof ObjectCreationExpr creation) {
            return mainSimpleNames.contains(creation.getType().getNameAsString());
        }
        if (expression instanceof FieldAccessExpr access && access.getScope().isThisExpr()) {
            String declared = localTypes.get(access.getNameAsString());
            return declared != null && mainSimpleNames.contains(declared);
        }
        return false;
    }

    private Map<String, String> localTypeNames(BlockStmt body, Model.Type owner) {
        Map<String, String> out = new LinkedHashMap<>();
        for (Model.Field field : owner.fields) {
            out.putIfAbsent(field.name, Names.simpleName(field.type));
        }
        for (Parameter parameter : body.findAll(Parameter.class)) {
            out.putIfAbsent(parameter.getNameAsString(),
                    Names.simpleName(Names.eraseGenerics(parameter.getType().asString())));
        }
        for (VariableDeclarator variable : body.findAll(VariableDeclarator.class)) {
            out.putIfAbsent(variable.getNameAsString(),
                    Names.simpleName(Names.eraseGenerics(variable.getType().asString())));
        }
        return out;
    }

    private String canonicalOwner(String qualifiedName) {
        if (mainTypes.containsKey(qualifiedName) || testTypes.containsKey(qualifiedName)) {
            return qualifiedName;
        }
        String dotted = qualifiedName.replace('$', '.');
        if (mainTypes.containsKey(dotted) || testTypes.containsKey(dotted)) {
            return dotted;
        }
        return qualifiedName;
    }

    private String paramSignature(ResolvedMethodDeclaration declaration) {
        StringBuilder out = new StringBuilder();
        try {
            for (int i = 0; i < declaration.getNumberOfParams(); i++) {
                if (i > 0) {
                    out.append(',');
                }
                String name = Names.resolvedName(declaration.getParam(i).getType());
                out.append(name == null ? "_" : Names.signatureErase(name));
            }
        } catch (Throwable failure) {
            return out + "_";
        }
        return out.toString();
    }

    private String unresolvedParamSignature(MethodCallExpr call) {
        StringBuilder out = new StringBuilder();
        List<Expression> arguments = call.getArguments();
        for (int i = 0; i < arguments.size(); i++) {
            if (i > 0) {
                out.append(',');
            }
            String name = null;
            try {
                name = Names.resolvedName(arguments.get(i).calculateResolvedType());
            } catch (Throwable failure) {
                name = null;
            }
            out.append(name == null ? "_" : Names.signatureErase(name));
        }
        return out.toString();
    }

    // ------------------------------------------------------------------ helpers

    private static ResolvedType resolveQuietly(Type type) {
        try {
            return type.resolve();
        } catch (Throwable failure) {
            return null;
        }
    }

    private static ResolvedReferenceTypeDeclaration referenceDeclaration(ResolvedType type) {
        if (type == null || !type.isReferenceType()) {
            return null;
        }
        try {
            return type.asReferenceType().getTypeDeclaration().orElse(null);
        } catch (Throwable failure) {
            return null;
        }
    }

    private static String visibility(boolean isPublic, boolean isProtected, boolean isPrivate,
                                    boolean implicitlyPublic) {
        if (isPrivate) {
            return "private";
        }
        if (isProtected) {
            return "protected";
        }
        if (isPublic) {
            return "public";
        }
        return implicitlyPublic ? "public" : "package";
    }

    private static boolean sameTypes(NodeList<Parameter> left, NodeList<Parameter> right) {
        if (left.size() != right.size()) {
            return false;
        }
        for (int i = 0; i < left.size(); i++) {
            if (!typeText(left.get(i).getType()).equals(typeText(right.get(i).getType()))) {
                return false;
            }
        }
        return true;
    }

    private static String typeText(Type type) {
        ResolvedType resolved = resolveQuietly(type);
        String name = resolved == null ? null : Names.resolvedName(resolved);
        return name == null ? Names.eraseGenerics(type.asString()) : name;
    }

    private static int beginLine(Node node) {
        return node.getRange().map(range -> range.begin.line).orElse(0);
    }

    private static int endLine(Node node) {
        return node.getRange().map(range -> range.end.line).orElse(beginLine(node));
    }

    /** A test-source compilation unit, reused by the test-extraction pass. */
    private static final class TestUnit {
        final RepoLayout.JavaFile file;
        final CompilationUnit unit;
        final Idioms idioms;

        TestUnit(RepoLayout.JavaFile file, CompilationUnit unit, Idioms idioms) {
            this.file = file;
            this.unit = unit;
            this.idioms = idioms;
        }
    }

    /** A model method plus the resolved data the classification pass needs. */
    private static final class MethodBuild {
        final Model.Method model;
        final ResolvedType returnType;
        final ResolvedReferenceTypeDeclaration returnDeclaration;

        MethodBuild(Model.Method model, ResolvedType returnType, ResolvedReferenceTypeDeclaration returnDeclaration) {
            this.model = model;
            this.returnType = returnType;
            this.returnDeclaration = returnDeclaration;
        }
    }
}
