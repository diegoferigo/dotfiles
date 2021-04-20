# [Choice] Ubuntu version: bionic, focal
ARG VARIANT=focal
FROM mcr.microsoft.com/vscode/devcontainers/base:${VARIANT}

ARG DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt-get update &&\
    apt-get install -y --no-install-recommends \
        nano \
        git \
        wget \
        curl \
        unzip \
        bash-completion \
    &&\
    rm -rf /var/lib/apt/lists/*

# Install fish
RUN apt-get update &&\
    apt-get install -y --no-install-recommends software-properties-common &&\
    apt-add-repository ppa:fish-shell/release-3 &&\
    apt-get install -y --no-install-recommends fish &&\
    rm -rf /var/lib/apt/lists/*
