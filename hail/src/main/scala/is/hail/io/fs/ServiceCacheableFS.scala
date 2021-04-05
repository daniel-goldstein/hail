package is.hail.io.fs

import java.io.InputStream

import is.hail.services.memory_client.MemoryClient
import org.apache.log4j.{LogManager, Logger}
import is.hail.utils._

trait ServiceCacheableFS extends FS {
  lazy val log: Logger = LogManager.getLogger("ServiceCacheableFS")

  def sessionID: String

  def getEtagOrNone(filename: String): Option[String]

  @transient private lazy val client: MemoryClient = {
    if (sessionID != null)
      MemoryClient.fromSessionID(sessionID)
    else MemoryClient.get
  }

  def openCachedNoCompression(filename: String): SeekableDataInputStream = {
    log.info("Checking to see if I should get cached thing")
    getEtagOrNone(filename).flatMap { etag =>
      log.info("Getting cached thing")
      client.open(filename, etag).map(new WrappedSeekableDataInputStream(_))
    }.getOrElse(openNoCompression(filename))
  }

  override def open(path: String, codec: CompressionCodec): InputStream =
    open(path, codec, cache = false)

  def open(path: String, codec: CompressionCodec, cache: Boolean): InputStream = {
    val is = if (cache) openCachedNoCompression(path) else openNoCompression(path)
    if (codec != null)
      codec.makeInputStream(is)
    else
      is
  }

  override def open(path: String, gzAsBGZ: Boolean): InputStream =
    open(path, getCodecFromPath(path, gzAsBGZ), cache = false)

  def open(path: String, gzAsBGZ: Boolean, cache: Boolean): InputStream =
    open(path, getCodecFromPath(path, gzAsBGZ))
}
