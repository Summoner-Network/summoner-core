from setuptools import setup, find_packages

# Read version without importing the package (safe for builds)
version_ns = {}
with open("summoner/_version.py", encoding="utf-8") as f:
    exec(f.read(), version_ns)

setup(
    name="summoner",
    version=version_ns["__version__"],
    description="Summoner's core SDK",
    author="Remy Tuyeras",
    author_email="rtuyeras@summoner.org",
    packages=find_packages(include=["summoner", "summoner.*"]),
    python_requires=">=3.9",
    install_requires=[
        "aioconsole==0.8.1",
        "python-dotenv==1.1.0",
        "typing_extensions==4.15.0; python_version < '3.13'",
    ],
    extras_require={
        "dev": [
            "maturin>=1.8",
            "pytest>=8.3.0",
            "black",
            "ruff",
        ]
    },
    include_package_data=True,
    zip_safe=False,
)
