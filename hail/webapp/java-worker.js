importScripts('sync-message.js')

async function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms))
}

onmessage = async (e) => {
    const writeChannel = e.data[0];
    const readChannel = e.data[1];
    let i = 0;
    while (true) {
        let message = readMessage(readChannel)
        await sleep(2000)
        writeMessage(writeChannel, `Executed ${JSON.stringify(message)}`)
    }
}
