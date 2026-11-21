from setuptools import setup, find_packages

setup(
    name="pulsewatch",
    version="0.1.0",
    description="Offline synthetic API traffic monitoring and anomaly detection system",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "pulsewatch = pulsewatch.cli:main",
        ],
    },
    python_requires=">=3.9",
)
