FROM ubuntu:22.04

# Avoid interactive prompts during package installs
ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies, Python 3.11, and cscope
RUN apt update && apt install -y \
    software-properties-common \
    curl \
    git \
    wget \
    dpkg \
    build-essential \
    libc6-dev \
    universal-ctags \
    cscope \
    bash-completion \
    lsb-release && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt update && apt install -y \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    && apt clean && rm -rf /var/lib/apt/lists/*

# Make python3 point to python3.11
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# Add support for 32-bit architecture
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y libc6-dev-i386 gcc-multilib

# Install CBMC
RUN wget \
    https://github.com/diffblue/cbmc/releases/download/cbmc-6.6.0/ubuntu-22.04-cbmc-6.6.0-Linux.deb \
    -O /tmp/ubuntu-24.04-cbmc-6.6.0-Linux.deb && \
    dpkg -i /tmp/ubuntu-24.04-cbmc-6.6.0-Linux.deb && \
    rm /tmp/ubuntu-24.04-cbmc-6.6.0-Linux.deb

RUN pip install cbmc-viewer

# Copy Makefile.include to /makefiles
COPY Makefile.include /makefiles/Makefile.include

COPY general-stubs.c /makefiles/general-stubs.c

ENV C_INCLUDE_PATH=/usr/include/x86_64-linux-gnu
