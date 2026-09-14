package com.demandtest.indexer;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;

/**
 * The handful of Maven POM facts the index needs (brief §3): artifact id, the {@code <modules>}
 * list, the compiler level and the test-framework/mocking dependencies. Parsed with the JDK DOM
 * parser; no dependency is added for this.
 */
final class Pom {

    final Path file;
    final String artifactId;
    final String javaVersion;
    final List<String> modules;
    final boolean junitJupiter;
    final boolean junit4;
    final boolean mockito;

    private Pom(Path file, String artifactId, String javaVersion, List<String> modules,
                boolean junitJupiter, boolean junit4, boolean mockito) {
        this.file = file;
        this.artifactId = artifactId;
        this.javaVersion = javaVersion;
        this.modules = modules;
        this.junitJupiter = junitJupiter;
        this.junit4 = junit4;
        this.mockito = mockito;
    }

    /** Returns {@code null} when the file is missing or unreadable — never throws. */
    static Pom parse(Path pomFile) {
        if (pomFile == null || !Files.isRegularFile(pomFile)) {
            return null;
        }
        try {
            DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
            factory.setNamespaceAware(false);
            factory.setValidating(false);
            trySetFeature(factory, "http://apache.org/xml/features/nonvalidating/load-external-dtd", false);
            trySetFeature(factory, "http://xml.org/sax/features/external-general-entities", false);
            trySetFeature(factory, "http://xml.org/sax/features/external-parameter-entities", false);
            DocumentBuilder builder = factory.newDocumentBuilder();
            builder.setEntityResolver((publicId, systemId) -> new org.xml.sax.InputSource(new java.io.StringReader("")));
            Document document = builder.parse(pomFile.toFile());
            Element project = document.getDocumentElement();
            if (project == null) {
                return null;
            }
            String artifactId = text(child(project, "artifactId"));
            List<String> modules = new ArrayList<>();
            Element modulesElement = child(project, "modules");
            if (modulesElement != null) {
                for (Element module : children(modulesElement, "module")) {
                    String name = text(module);
                    if (name != null && !name.isEmpty()) {
                        modules.add(name);
                    }
                }
            }
            Map<String, String> properties = new LinkedHashMap<>();
            Element propertiesElement = child(project, "properties");
            if (propertiesElement != null) {
                for (Element property : children(propertiesElement, null)) {
                    properties.put(property.getTagName(), text(property));
                }
            }
            String javaVersion = firstNonPlaceholder(
                    properties.get("maven.compiler.release"),
                    properties.get("maven.compiler.source"),
                    properties.get("maven.compiler.target"),
                    properties.get("java.version"));
            boolean jupiter = false;
            boolean junit4 = false;
            boolean mockito = false;
            Element dependencies = child(project, "dependencies");
            if (dependencies != null) {
                for (Element dependency : children(dependencies, "dependency")) {
                    String depArtifact = text(child(dependency, "artifactId"));
                    if (depArtifact == null) {
                        continue;
                    }
                    if (depArtifact.startsWith("junit-jupiter")) {
                        jupiter = true;
                    } else if (depArtifact.equals("junit")) {
                        junit4 = true;
                    } else if (depArtifact.startsWith("mockito")) {
                        mockito = true;
                    }
                }
            }
            return new Pom(pomFile, artifactId, javaVersion, modules, jupiter, junit4, mockito);
        } catch (Throwable failure) {
            return null;
        }
    }

    private static void trySetFeature(DocumentBuilderFactory factory, String feature, boolean value) {
        try {
            factory.setFeature(feature, value);
        } catch (Throwable ignored) {
            // Feature unsupported by this parser: harmless, the defaults are safe for a POM.
        }
    }

    private static String firstNonPlaceholder(String... candidates) {
        for (String candidate : candidates) {
            if (candidate != null && !candidate.isEmpty() && !candidate.contains("${")) {
                return candidate;
            }
        }
        return null;
    }

    private static Element child(Element parent, String name) {
        for (Element element : children(parent, name)) {
            return element;
        }
        return null;
    }

    private static List<Element> children(Element parent, String name) {
        List<Element> out = new ArrayList<>();
        NodeList nodes = parent.getChildNodes();
        for (int i = 0; i < nodes.getLength(); i++) {
            Node node = nodes.item(i);
            if (node.getNodeType() != Node.ELEMENT_NODE) {
                continue;
            }
            if (name == null || name.equals(node.getNodeName())) {
                out.add((Element) node);
            }
        }
        return out;
    }

    private static String text(Element element) {
        if (element == null) {
            return null;
        }
        String value = element.getTextContent();
        return value == null ? null : value.trim();
    }

}
