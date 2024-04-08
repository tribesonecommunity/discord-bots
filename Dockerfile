# syntax=docker/dockerfile:1

FROM technitaur/tribesbot:base as build
WORKDIR /tribesbot
USER tribesbot
COPY . .