from setuptools import setup, find_packages

setup(
    name="summoner",
    version="0.1.0",
    description="Summoner's core SDK",
    author="Remy Tuyeras, Elliott Dehnbostel",
    author_email="rtuyeras@summoner.to",
    packages=find_packages(include=["summoner", "summoner.*"]),
    install_requires=[
        "aioconsole==0.8.1",
        "python-dotenv==1.1.0",
        "httpx==0.28.1"
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
