FROM docker.io/library/node:22-bookworm
WORKDIR /src
# Image for the stream-donations distrobox. Node installs the Electron shell.
# The donation collector runs with the host python3 / pyenv, not in this image.
