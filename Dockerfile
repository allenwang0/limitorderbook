Use a lightweight GCC image

FROM gcc:latest

Set working directory

WORKDIR /usr/src/lob_sim

Copy source files

COPY . .

Compile the application

RUN make

Run the benchmark when container starts

CMD ["./lob_sim"]