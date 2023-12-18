importScripts('sync-message.js')

onmessage = (e) => {
    const channel = e.data;
    const message = readMessage(channel, { checkTimeout: 100, timeout: NUMBER.POSITIVE_INFINITY });
}
