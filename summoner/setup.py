from setuptools import setup, find_packages

setup(
    name="summoner",
    version="0.1.0",
    description="Summoner's SDK",
    author="Remy Tuyeras, Elliott Dehnbostel",
    author_email="rtuyeras@summoner.to",
    packages=find_packages(),
    # packages=find_packages(include=["summoner", "summoner.*"]),
    install_requires=[
        "asyncio==3.4.3",
        "aioconsole==0.8.1",
        "python-dotenv==0.9.0"
    ],
    extras_require={
        "dev": [
            "maturin>=1.8",
            "pytest>=8.3.0",
            "black",
            "ruff"
        ]
    },
    include_package_data=True,
    zip_safe=False,
)
