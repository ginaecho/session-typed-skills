# Wrapper image for the coinductive nuscr fork (phou/nuscr_coinduction).
#
# The fork's own Dockerfile does not install the protobuf depext that its
# nuscr.opam now requires (conf-protoc -> libprotobuf-dev + protobuf-compiler),
# so `opam install ./nuscr.opam` fails there. We keep the vendored checkout a
# clean upstream copy and add the missing system packages here instead.
#
# Build context must be the fork checkout:
#   docker build -t nuscr-coind:latest -f tools/nuscr/Dockerfile ./nuscr-coinduction
FROM ocaml/opam

RUN sudo apt-get update \
  && sudo apt-get install -y m4 python3 libgmp-dev libprotobuf-dev protobuf-compiler pkg-config \
  && sudo rm -rf /var/lib/apt/lists/* /tmp/*

COPY --chown=opam:opam ./ $HOME/nuscr

WORKDIR $HOME/nuscr

RUN opam install -y ./nuscr.opam

ENTRYPOINT ["opam", "exec", "--", "nuscr"]
