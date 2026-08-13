from setuptools import find_packages, setup


setup(
    name="marketing-knowledge-agent",
    version="0.1.0",
    description="Offline RAG prototype for an Obsidian Markdown marketing knowledge vault.",
    package_dir={"": "src"},
    packages=find_packages("src"),
    install_requires=["google-auth[requests]>=2.50,<2.51", "pydantic>=1.10,<3"],
    extras_require={"dev": ["pytest>=7"]},
    entry_points={"console_scripts": ["mka=marketing_knowledge_agent.cli:main"]},
    python_requires=">=3.9",
)
