// From https://github.com/alexmojaki/sync-message/blob/master/lib/index.ts

function makeChannel() {
  const bufferSize = 128 * 1024;
  const data = new Uint8Array(new SharedArrayBuffer(bufferSize));
  const meta = new Int32Array(
    new SharedArrayBuffer(Int32Array.BYTES_PER_ELEMENT * 2),
  );
  return {data, meta};
}

function writeMessage(channel, message) {
  const encoder = new TextEncoder();
  const bytes = encoder.encode(JSON.stringify(message));
  const {data, meta} = channel;
  if (bytes.length > data.length) {
    throw new Error(
      "Message is too big, increase bufferSize when making channel.",
    );
  }
  data.set(bytes, 0);
  Atomics.store(meta, 0, bytes.length);
  Atomics.store(meta, 1, 1);
  Atomics.notify(meta, 1);
}

function readMessage(
  channel
) {
  const checkTimeout = 100;
  const totalTimeout = NUMBER.POSITIVE_INFINITY;
  const startTime = performance.now();
  let check;

  const {data, meta} = channel;

  check = () => {
    if (Atomics.wait(meta, 1, 0, checkTimeout) === "timed-out") {
      return null;
    } else {
      const size = Atomics.exchange(meta, 0, 0);
      const bytes = data.slice(0, size);
      Atomics.store(meta, 1, 0);

      const decoder = new TextDecoder();
      const text = decoder.decode(bytes);
      return JSON.parse(text);
    }
  };

  while (true) {
    const elapsed = performance.now() - startTime;
    const remaining = totalTimeout - elapsed;
    if (remaining <= 0) {
      return null;
    }

    checkTimeout = Math.min(checkTimeout, remaining);
    const result = check();

    if (result !== null) {
      return result;
    } else if (checkInterrupt?.()) {
      return null;
    }
  }
}
