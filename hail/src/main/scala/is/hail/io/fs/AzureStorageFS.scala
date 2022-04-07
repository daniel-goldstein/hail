package is.hail.io.fs

import is.hail.utils._

import com.azure.core.credential.{TokenCredential, TokenRequestContext}
import com.azure.identity.{ClientSecretCredential, ClientSecretCredentialBuilder, DefaultAzureCredential, DefaultAzureCredentialBuilder}
import com.azure.storage.blob.models.{BlobProperties, BlobRange, ListBlobsOptions, BlobItem}
import com.azure.storage.blob.specialized.AppendBlobClient
import com.azure.storage.blob.{BlobClient, BlobContainerClient, BlobServiceClient, BlobServiceClientBuilder}
import is.hail.io.fs.AzureStorageFS.getAccountContainerPath
import is.hail.io.fs.FSUtil.{containsWildcard, dropTrailingSlash}
import org.apache.http.client.methods.HttpGet
import org.apache.http.impl.client.{CloseableHttpClient, HttpClients}
import org.apache.log4j.Logger

import java.net.URI
import is.hail.utils.fatal
import org.json4s
import org.json4s.jackson.JsonMethods

import java.io.{ByteArrayInputStream, FileNotFoundException, OutputStream}
import java.nio.file.FileSystems
import java.time.Duration
import scala.collection.mutable
import scala.collection.mutable.ArrayBuffer
import com.azure.core.credential.AccessToken
import org.apache.http.HttpEntity


object AzureStorageFS {
  private val pathRegex = "/([^/]+)(.*)".r

  private val log = Logger.getLogger(getClass.getName)

  val schemes: Array[String] = Array("hail-az")

  def getAccountContainerPath(filename: String): (String, String, String) = {
    val uri = new URI(filename).normalize()

    val scheme = uri.getScheme
    if (scheme == null || !schemes.contains(scheme)) {
      throw new IllegalArgumentException(s"invalid scheme, expected hail-az: $scheme")
    }

    val account = uri.getHost
    if (account == null) {
      throw new IllegalArgumentException(s"Invalid path, expected hail-az://accountName/containerName/blobPath: $filename")
    }

    val (container, path) = pathRegex.findFirstMatchIn(uri.getPath) match {
      case Some(filenameMatch) =>
        val container = filenameMatch.group(1)
        val path = filenameMatch.group(2)
        if (path != "") {
          assert(path.startsWith("/"))
          (container, path.substring(1))
        }
        else {
          (container, "")
        }
      case None =>
          fatal(s"filename $filename is not in the correct format. hail-az://account/container/blobPath")
    }

    (account, container, path)
  }
}

object AzureStorageFileStatus {
  def apply(blobProperties: BlobProperties, path: String, isDir: Boolean): BlobStorageFileStatus = {
    val modificationTime = blobProperties.getLastModified.toEpochSecond
    val size = blobProperties.getBlobSize

    new BlobStorageFileStatus(path, modificationTime, size, isDir)
  }
}

class AzureBlobServiceClientCache(credential: TokenCredential) {
  @transient private lazy val clientBuilder: BlobServiceClientBuilder = new BlobServiceClientBuilder()
  @transient private lazy val clients: mutable.Map[String, BlobServiceClient] = mutable.Map()
  @transient private lazy val httpClient: CloseableHttpClient = HttpClients.custom().build()

  def getServiceClient(account: String): BlobServiceClient = {
    clients.get(account) match {
      case Some(client) => client
      case None =>
        val blobServiceClient = clientBuilder
          .credential(credential)
          .endpoint(s"https://$account.blob.core.windows.net")
          .buildClient()
        clients += (account -> blobServiceClient)
        blobServiceClient
    }
  }

  def getHttpClient: CloseableHttpClient = httpClient
  def getAccessToken: AccessToken = {
    val ctx = new TokenRequestContext()
    ctx.addScopes("https://storage.azure.com/.default")
    credential.getToken(ctx).block()
  }
}


class AzureStorageFS(val credentialsJSON: Option[String] = None) extends FS {
  @transient private lazy val serviceClientCache = credentialsJSON match {
    case None =>
      val credential: DefaultAzureCredential = new DefaultAzureCredentialBuilder().build()
      new AzureBlobServiceClientCache(credential)
    case Some(keyData) =>
      val kvs = JsonMethods.parse(keyData) match {
        case json4s.JObject(values) => values.toMap
      }

      val appId = kvs("appId").asInstanceOf[json4s.JString].s
      val password = kvs("password").asInstanceOf[json4s.JString].s
      val tenant = kvs("tenant").asInstanceOf[json4s.JString].s

      val clientSecretCredential: ClientSecretCredential = new ClientSecretCredentialBuilder()
        .clientId(appId)
        .clientSecret(password)
        .tenantId(tenant)
        .build()
      val tokenRequestContext = new TokenRequestContext()
      tokenRequestContext.addScopes("https://storage.azure.com/.default")
      new AzureBlobServiceClientCache(clientSecretCredential)
  }

  def getBlobServiceClient(account: String): BlobServiceClient = {
    serviceClientCache.getServiceClient(account)
  }

  def getBlobClient(account: String, container: String, path: String): BlobClient = {
    getBlobServiceClient(account).getBlobContainerClient(container).getBlobClient(path)
  }

  def getContainerClient(account: String, container: String): BlobContainerClient = {
    getBlobServiceClient(account).getBlobContainerClient(container)
  }

  private[this] def getHttpClient = serviceClientCache.getHttpClient
  private[this] def listBlobsWithPrefix(account: String, container: String, path: String)(f: BlobItem => Unit): Unit = {
    // val httpClient = serviceClientCache.getHttpClient
    // val token = serviceClientCache.getAccessToken
    // val req = new HttpGet()
    // req.addHeader("Authorization", s"Bearer: ${token}")
    // using(httpClient.execute(req)) { resp =>
    //   val statusCode = resp.getStatusLine.getStatusCode
    //   log.info(s"request ${ req.getMethod } ${ req.getURI } response $statusCode")
    //   resp.getEntity().getContent()
    // }
    val prefixMatches = getContainerClient(account, container).listBlobsByHierarchy(path).iterator()
    var firstMatch: BlobItem = null
    var looped = false

    var i = 0
    while (prefixMatches.hasNext && !looped && i < 100) {
      val blobItem = prefixMatches.next
      if (blobItem.getName.equals(firstMatch.getName)) {
        looped = true
      } else {
        if (firstMatch == null) {
          firstMatch = blobItem
        }
        f(blobItem)
      }
      i += 1
    }
  }

  def openNoCompression(filename: String): SeekableDataInputStream = {
    val (account, container, path) = getAccountContainerPath(filename)
    val blobClient: BlobClient = getBlobClient(account, container, path)
    val blobProperties = blobClient.getProperties
    val blobSize = blobProperties.getBlobSize

    val is: SeekableInputStream = new FSSeekableInputStream {
      private[this] val client: BlobClient = blobClient

      def fill(): Unit = {
        bb.clear()

        val outputStreamToBuffer: OutputStream = (i: Int) => {
          bb.put(i.toByte)
        }

        // calculate the minimum of the bytes remaining in the buffer and the bytes remaining
        // to be read in the blob
        val count = Math.min(blobSize - pos, bb.remaining())
        if (count <= 0) {
          eof = true
          return
        }

        client.downloadWithResponse(
          outputStreamToBuffer, new BlobRange(pos, count),
          null, null, false, Duration.ofMinutes(1), null)
        pos += count
        bb.flip()

        assert(bb.position() == 0 && bb.remaining() > 0)
      }

      override def seek(newPos: Long): Unit = {
        bb.clear()
        bb.limit(0)
        pos = newPos
      }
    }

    new WrappedSeekableDataInputStream(is)
  }

  def createNoCompression(filename: String): PositionedDataOutputStream = {
    val (account, container, path) = getAccountContainerPath(filename)
    val appendClient: AppendBlobClient = getBlobClient(account, container, path).getAppendBlobClient
    if (!appendClient.exists()) appendClient.create()

    val os: PositionedOutputStream = new FSPositionedOutputStream {
      private[this] val client: AppendBlobClient = appendClient

      override def flush(): Unit = {
        bb.flip()

        if (bb.limit() > 0) {
          client.appendBlock(new ByteArrayInputStream(bb.array(), 0, bb.limit()), bb.limit())
        }

        bb.clear()
      }

      override def close(): Unit = {
        if (!closed) {
          flush()
          closed = true
        }
      }
    }

    new WrappedPositionedDataOutputStream(os)
  }

  def delete(filename: String, recursive: Boolean): Unit = {
    val (account, container, path) = getAccountContainerPath(filename)
    if (recursive) {
      listBlobsWithPrefix(account, container, path) { blobItem =>
        getBlobClient(account, container, blobItem.getName).delete()
      }
    }
    else {
        getBlobClient(account, container, path).delete()
    }
  }

  def listStatus(filename: String): Array[FileStatus] = {
    val (account, container, path) = getAccountContainerPath(filename)

    val blobContainerClient: BlobContainerClient = getContainerClient(account, container)
    val statList: ArrayBuffer[FileStatus] = ArrayBuffer()

    listBlobsWithPrefix(account, container, path) { blobItem =>
      statList += fileStatus(account, container, blobItem.getName)
    }
    statList.toArray
  }

  def glob(filename: String): Array[FileStatus] = {
    var (account, container, path) = getAccountContainerPath(filename)
    path = dropTrailingSlash(path)

    globWithPrefix(prefix = s"hail-az://$account/$container", path = path)
  }

  def fileStatus(account: String, container: String, path: String): FileStatus = {
    if (path == "") {
      return new BlobStorageFileStatus(s"hail-az://$account/$container", null, 0, true)
    }

    val blobClient: BlobClient = getBlobClient(account, container, path)
    val blobContainerClient: BlobContainerClient = getContainerClient(account, container)

    // TODO GROSS
    var isDir = false
    listBlobsWithPrefix(account, container, path) { _ =>
      isDir = true
    }

    val filename = dropTrailingSlash(s"hail-az://$account/$container/$path")
    if (!isDir && !blobClient.exists()) throw new FileNotFoundException(s"File not found: $filename")

    if (isDir) {
      new BlobStorageFileStatus(path = filename, -1, -1, isDir = true)
    }
    else {
      val blobProperties: BlobProperties = blobClient.getProperties
      AzureStorageFileStatus(blobProperties, path = filename, isDir = false)
    }
  }

  def fileStatus(filename: String): FileStatus = {
    val (account, container, path) = getAccountContainerPath(filename)
    fileStatus(account, container, path)
  }

  def makeQualified(filename: String): String = {
    if (!filename.startsWith("hail-az://"))
      throw new IllegalArgumentException(s"Invalid path, expected hail-az://accountName/containerName/blobPath: $filename")
    filename
  }
}
