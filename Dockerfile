# syntax=docker/dockerfile:1

FROM debian:stable-slim as debinstall
SHELL [ "/bin/bash", "-c" ]
RUN apt-get update

# I have occasionally run into situations where it stopped being able
# to get packages if I pulled too many in a single command. I think
# this may be a quirk specific to Docker containers. This is why I've
# split it into multiple apt-gets here.

RUN --mount=type=cache,uid=0,gid=0,target=/var/cache/apt apt-get --yes install python3 python3-venv python3-pip python3-dev
RUN --mount=type=cache,uid=0,gid=0,target=/var/cache/apt apt-get --yes install g++ make vim
RUN --mount=type=cache,uid=0,gid=0,target=/var/cache/apt apt-get --yes install libpq-dev libjpeg-dev libffi-dev g++ make vim

FROM debinstall as usersetup
RUN groupadd -g 999 tribesbot
RUN useradd --system --create-home --home-dir /tribesbot -u 999 -g tribesbot tribesbot

FROM usersetup as requirements
WORKDIR /tribesbot
USER tribesbot
COPY requirements.txt .
COPY setup.py .
COPY startup.sh .
RUN python3 -m venv .venv
ENV PATH=".venv/bin:$PATH"
RUN pip install --upgrade pip
RUN --mount=type=cache,uid=999,gid=999,target=/tribesbot/.cache/pip pip install -U .

FROM requirements as base
# remove unnecessary packages from the image after everything has been built
USER root
RUN apt-get --yes autoremove python3-venv python3-dev libffi-dev g++ make python3-pip python3-dev

FROM base as build
WORKDIR /tribesbot
USER tribesbot
