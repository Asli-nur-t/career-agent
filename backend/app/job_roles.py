"""Shared role catalogue used by the API and browser-agent title filter."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JobRoleDefinition:
    name: str
    aliases: tuple[str, ...] = ()


ROLE_CATALOG: tuple[JobRoleDefinition, ...] = (
    JobRoleDefinition(
        "AI Engineer",
        (
            "AI Engineering",
            "Artificial Intelligence Engineer",
            "Yapay Zeka Mühendisi",
            "Yapay Zeka Stajyeri",
        ),
    ),
    JobRoleDefinition(
        "Machine Learning Engineer",
        ("ML Engineer", "Makine Öğrenmesi Mühendisi"),
    ),
    JobRoleDefinition(
        "GenAI Engineer",
        ("Generative AI Engineer", "Generative Artificial Intelligence Engineer"),
    ),
    JobRoleDefinition("RAG Engineer", ("Retrieval Augmented Generation Engineer",)),
    JobRoleDefinition(
        "LLM Engineer",
        ("Large Language Model Engineer", "Large Language Models Engineer"),
    ),
    JobRoleDefinition("Applied AI Engineer", ("AI Application Engineer",)),
    JobRoleDefinition(
        "AI Research Engineer",
        ("AI Researcher", "Machine Learning Research Engineer"),
    ),
    JobRoleDefinition(
        "Computer Vision Engineer",
        ("CV Engineer", "Computer Vision Developer", "Görüntü İşleme Mühendisi"),
    ),
    JobRoleDefinition("Deep Learning Engineer", ("Deep Learning Developer",)),
    JobRoleDefinition(
        "NLP Engineer",
        ("Natural Language Processing Engineer", "Doğal Dil İşleme Mühendisi"),
    ),
    JobRoleDefinition("Prompt Engineer", ("AI Prompt Engineer",)),
    JobRoleDefinition("Data Scientist", ("Veri Bilimci", "Veri Bilimi Uzmanı")),
    JobRoleDefinition("Data Engineer", ("Veri Mühendisi",)),
    JobRoleDefinition("Analytics Engineer", ("Data Analytics Engineer",)),
    JobRoleDefinition("MLOps Engineer", ("MLOps Specialist",)),
    JobRoleDefinition("Data Analyst", ("Veri Analisti",)),
    JobRoleDefinition(
        "Business Intelligence Analyst",
        ("BI Analyst", "İş Zekası Analisti"),
    ),
    JobRoleDefinition("BI Developer", ("Business Intelligence Developer",)),
    JobRoleDefinition("Database Developer", ("SQL Developer", "Veritabanı Geliştirici")),
    JobRoleDefinition("Database Administrator", ("DBA", "Veritabanı Yöneticisi")),
    JobRoleDefinition(
        "Software Engineer",
        ("Software Developer", "Yazılım Mühendisi", "Yazılım Geliştirici"),
    ),
    JobRoleDefinition("Junior Software Engineer", ("Junior Software Developer",)),
    JobRoleDefinition(
        "Backend Engineer",
        ("Backend Developer", "Back End Engineer", "Back End Developer"),
    ),
    JobRoleDefinition("Backend Developer", ("Server Side Developer",)),
    JobRoleDefinition("API Developer", ("API Engineer", "Integration Developer")),
    JobRoleDefinition("Python Developer", ("Python Engineer",)),
    JobRoleDefinition(".NET Developer", ("Dotnet Developer", ".NET Engineer")),
    JobRoleDefinition("C# Developer", ("C Sharp Developer", "C# Engineer")),
    JobRoleDefinition("Java Developer", ("Java Engineer",)),
    JobRoleDefinition(
        "Frontend Developer",
        ("Frontend Engineer", "Front End Developer", "Front End Engineer"),
    ),
    JobRoleDefinition("React Developer", ("React.js Developer", "React Engineer")),
    JobRoleDefinition(
        "Full Stack Developer",
        ("Full Stack Engineer", "Fullstack Developer", "Fullstack Engineer"),
    ),
    JobRoleDefinition(
        "Mobile Developer",
        ("Mobile Application Developer", "Mobil Uygulama Geliştirici"),
    ),
    JobRoleDefinition("Flutter Developer", ("Flutter Engineer",)),
    JobRoleDefinition("iOS Developer", ("iOS Engineer", "Swift Developer")),
    JobRoleDefinition("Android Developer", ("Android Engineer", "Kotlin Developer")),
    JobRoleDefinition(
        "Business Analyst",
        ("IT Business Analyst", "İş Analisti", "Bilgi Teknolojileri İş Analisti"),
    ),
    JobRoleDefinition("IT Business Analyst", ("Technical Business Analyst",)),
    JobRoleDefinition("System Analyst", ("Systems Analyst", "Sistem Analisti")),
    JobRoleDefinition("Product Analyst", ("Ürün Analisti",)),
    JobRoleDefinition("Product Manager", ("Product Owner", "Ürün Yöneticisi")),
    JobRoleDefinition(
        "Technical Product Manager",
        ("Technical Product Owner", "Teknik Ürün Yöneticisi"),
    ),
    JobRoleDefinition("System Engineer", ("Systems Engineer", "Sistem Mühendisi")),
    JobRoleDefinition("DevOps Engineer", ("DevOps Specialist",)),
    JobRoleDefinition(
        "Site Reliability Engineer",
        ("SRE Engineer", "Site Reliability Specialist"),
    ),
    JobRoleDefinition("Platform Engineer", ("Platform Developer",)),
    JobRoleDefinition("Cloud Engineer", ("Cloud Infrastructure Engineer", "Bulut Mühendisi")),
    JobRoleDefinition(
        "Kubernetes Engineer",
        ("K8s Engineer", "Kubernetes Platform Engineer"),
    ),
    JobRoleDefinition("Solutions Engineer", ("Solution Engineer", "Çözüm Mühendisi")),
    JobRoleDefinition(
        "QA Engineer",
        ("Quality Assurance Engineer", "Software Quality Engineer"),
    ),
    JobRoleDefinition("Software Test Engineer", ("Test Engineer", "Yazılım Test Mühendisi")),
    JobRoleDefinition("Test Automation Engineer", ("QA Automation Engineer",)),
    JobRoleDefinition(
        "Cyber Security Engineer",
        ("Cybersecurity Engineer", "Siber Güvenlik Mühendisi"),
    ),
    JobRoleDefinition(
        "Information Security Specialist",
        ("Information Security Analyst", "Bilgi Güvenliği Uzmanı"),
    ),
    JobRoleDefinition("Network Engineer", ("Network Specialist", "Ağ Mühendisi")),
    JobRoleDefinition("ERP Consultant", ("ERP Specialist", "ERP Danışmanı")),
    JobRoleDefinition("SAP Consultant", ("SAP Specialist", "SAP Danışmanı", "SAP Uzmanı")),
    JobRoleDefinition("CRM Specialist", ("CRM Consultant", "CRM Uzmanı")),
    JobRoleDefinition(
        "Implementation Consultant",
        ("Implementation Specialist", "Uygulama Danışmanı"),
    ),
    JobRoleDefinition(
        "Technical Support Engineer",
        ("Technical Support Specialist", "Teknik Destek Mühendisi"),
    ),
)


ROLE_ALIAS_GROUPS: tuple[tuple[str, ...], ...] = tuple(
    (definition.name, *definition.aliases) for definition in ROLE_CATALOG
)


def role_catalog_names() -> list[str]:
    return [definition.name for definition in ROLE_CATALOG]
