importScripts('https://cjrtnc.leaningtech.com/3.0rc2/cj3loader.js')
importScripts('sync-message.js')

var w = new CheerpJWorker();

onmessage = async (e) => {
    await w.cheerpjInit()

    const writeChannel = e.data[0];
    const readChannel = e.data[1];
    let i = 0;
    while (i < 100) {
        let message = readMessage(readChannel)
        writeMessage(writeChannel, `Executed ${JSON.stringify(message)}`)
        i += 1;
    }
}
