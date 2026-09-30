FROM ubuntu:20.04

ARG DEBIAN_FRONTEND=noninteractive
ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=$USER_UID

RUN sed -i '$c\deb [arch=amd64] http://archive.ubuntu.com/ubuntu focal main universe' /etc/apt/sources.list

RUN apt-get update \
	&& apt-get install -y --no-install-recommends \
		ca-certificates \
		git \
		make \
		software-properties-common \
		sudo \
	&& add-apt-repository ppa:ubuntu-toolchain-r/test \
	&& apt-get update \
	&& apt-get install -y --no-install-recommends \
		gcc-7 \
		g++-7 \
	&& update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-7 70 \
	&& update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-7 70 \
	&& update-alternatives --install /usr/bin/cc cc /usr/bin/gcc-7 70 \
	&& update-alternatives --install /usr/bin/c++ c++ /usr/bin/g++-7 70 \
	&& apt-get clean \
	&& rm -rf /var/lib/apt/lists/*

RUN groupadd --gid ${USER_GID} ${USERNAME} \
	&& useradd --uid ${USER_UID} --gid ${USER_GID} --create-home --shell /bin/bash ${USERNAME} \
	&& usermod --append --groups sudo ${USERNAME} \
	&& echo "${USERNAME} ALL=(root) NOPASSWD:ALL" > /etc/sudoers.d/${USERNAME} \
	&& chmod 0440 /etc/sudoers.d/${USERNAME}

USER ${USERNAME}
WORKDIR /workspaces/pa2

