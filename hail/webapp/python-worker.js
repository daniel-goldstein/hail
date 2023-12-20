importScripts('https://cdn.jsdelivr.net/pyodide/dev/full/pyodide.js')
importScripts('sync-message.js')

const worker = new Worker("java-worker.js")
const readChannel = makeChannel()
const writeChannel = makeChannel()
worker.postMessage([readChannel, writeChannel])

globalThis.callOnBackend = (method, args) => {
    writeMessage(writeChannel, {method, args})
    return readMessage(readChannel)
}

(async function run() {
    let pyodide = await loadPyodide()
    await pyodide.runPythonAsync(`
        from js import callOnBackend

        print("Sending...")
        res = callOnBackend("foo", 1)
        print("Received ", res)

        print("Sending...")
        res = callOnBackend("foo", 2)
        print("Received ", res)
    `)
})()
