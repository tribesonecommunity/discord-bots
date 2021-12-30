from setuptools import find_packages, setup

with open('requirements.txt') as f:
    requirements = f.read().splitlines()

setup(
    name='discord_bots',
    install_requires=requirements,
    packages=find_packages(),
    version='1.0',
)
