from setuptools import setup, find_packages

setup(
    name="summoner",
    version="0.1.0",
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
