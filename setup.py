from setuptools import find_packages, setup

with open('requirements.txt') as f:
    requirements = f.read().splitlines()

setup(
    name='discord_bots',
    entry_points = {
        'console_scripts': ['run-discord-bot=discord_bots.main:main'],
    },
    install_requires=requirements,
    packages=find_packages(),
    version='1.0',
)
