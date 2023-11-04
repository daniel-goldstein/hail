package is.hail.backend.service

import is.hail.{HailContext, HailFeatureFlags}
import is.hail.annotations._
import is.hail.asm4s._
import is.hail.backend._
import is.hail.expr.Validate
import is.hail.expr.ir.{
  Compile, IR, IRParser, LoweringAnalyses, MakeTuple, SortField, TableIR, TableReader, TypeCheck,
}
import is.hail.expr.ir.analyses.SemanticHash
import is.hail.expr.ir.functions.IRFunctionRegistry
import is.hail.expr.ir.lowering._
import is.hail.io.{BufferSpec, TypedCodecSpec}
import is.hail.io.fs._
import is.hail.linalg.BlockMatrix
import is.hail.services._
import is.hail.services.batch_client.BatchClient
import is.hail.types._
import is.hail.types.encoded._
import is.hail.types.physical._
import is.hail.types.physical.stypes.PTypeReferenceSingleCodeType
import is.hail.types.virtual._
import is.hail.utils._
import is.hail.variant.ReferenceGenome

import scala.annotation.switch
import scala.collection.JavaConverters._
import scala.reflect.ClassTag

import java.io._
import java.nio.charset.StandardCharsets
import java.util.concurrent._

import org.apache.log4j.Logger
import org.json4s.{DefaultFormats, Formats}
import org.json4s.JsonAST._
import org.json4s.jackson.JsonMethods

class ServiceBackendContext(
  val billingProject: String,
  val remoteTmpDir: String,
  val workerCores: String,
  val workerMemory: String,
  val storageRequirement: String,
  val regions: Array[String],
  val cloudfuseConfig: Array[CloudfuseConfig],
  val profile: Boolean,
  val executionCache: ExecutionCache,
) extends BackendContext with Serializable {}

object ServiceBackend {
  private val log = Logger.getLogger(getClass.getName())

  def apply(
    image: String,
    name: String,
    theHailClassLoader: HailClassLoader,
    batchClient: BatchClient,
    batchId: Option[Long],
    rpcConfig: ServiceBackendRPCPayload,
  ): ServiceBackend = {
    HailContext.configureStderrLogging()

    log.info("Starting ServiceBackend.apply")
    val flags = HailFeatureFlags.fromMap(rpcConfig.flags)
    log.info("Made flags")
    val shouldProfile = flags.get("profile") != null
    val fs = FS.cloudSpecificFS(Some(flags))
    log.info("Made FS")

    val backendContext = new ServiceBackendContext(
      rpcConfig.billing_project,
      rpcConfig.remote_tmpdir,
      rpcConfig.worker_cores,
      rpcConfig.worker_memory,
      rpcConfig.storage,
      rpcConfig.regions,
      rpcConfig.cloudfuse_configs,
      shouldProfile,
      ExecutionCache.fromFlags(flags, fs, rpcConfig.remote_tmpdir),
    )
    log.info("Made backend context")

    val backend = new ServiceBackend(
      image,
      name,
      new HailClassLoader(getClass().getClassLoader()),
      batchClient,
      batchId,
      flags,
      rpcConfig.tmp_dir,
      fs,
      backendContext,
    )
    backend.addDefaultReferences()

    rpcConfig.custom_references.foreach(s => backend.addReference(ReferenceGenome.fromJSON(s)))
    rpcConfig.liftovers.foreach { case (sourceGenome, liftoversForSource) =>
      liftoversForSource.foreach { case (destGenome, chainFile) =>
        backend.addLiftover(sourceGenome, chainFile, destGenome)
      }
    }
    rpcConfig.sequences.foreach { case (rg, seq) =>
      backend.addSequence(rg, seq.fasta, seq.index)
    }

    log.info("Finish apply")
    backend
  }
}

class ServiceBackend(
  val image: String,
  var name: String,
  val theHailClassLoader: HailClassLoader,
  val batchClient: BatchClient,
  val curBatchId: Option[Long],
  val flags: HailFeatureFlags,
  val tmpdir: String,
  val fs: FS,
  val serviceBackendContext: ServiceBackendContext,
) extends Backend with BackendWithNoCodeCache {
  import ServiceBackend.log

  private[this] var stageCount = 0
  private[this] val MAX_AVAILABLE_GCS_CONNECTIONS = 1000
  private[this] val executor = Executors.newFixedThreadPool(MAX_AVAILABLE_GCS_CONNECTIONS)

  override def shouldCacheQueryInfo: Boolean = false

  def defaultParallelism: Int = 4

  def broadcast[T: ClassTag](_value: T): BroadcastValue[T] = {
    using(new ObjectOutputStream(new ByteArrayOutputStream())) { os =>
      try
        os.writeObject(_value)
      catch {
        case e: Exception =>
          fatal(_value.toString, e)
      }
    }
    new BroadcastValue[T] with Serializable {
      def value: T = _value
    }
  }

  private[this] def readString(in: DataInputStream): String = {
    val n = in.readInt()
    val bytes = new Array[Byte](n)
    in.read(bytes)
    new String(bytes, StandardCharsets.UTF_8)
  }

  private[this] def submitAndWaitForBatch(
    _backendContext: BackendContext,
    fs: FS,
    collection: Array[Array[Byte]],
    stageIdentifier: String,
    f: (Array[Byte], HailTaskContext, HailClassLoader, FS) => Array[Byte],
  ): (String, String, Int) = {
    val backendContext = _backendContext.asInstanceOf[ServiceBackendContext]
    val n = collection.length
    val token = tokenUrlSafe(32)
    val root = s"${backendContext.remoteTmpDir}parallelizeAndComputeWithIndex/$token"

    log.info(s"parallelizeAndComputeWithIndex: $token: nPartitions $n")
    log.info(s"parallelizeAndComputeWithIndex: $token: writing f and contexts")

    val uploadFunction = executor.submit[Unit](() =>
      retryTransientErrors {
        fs.writePDOS(s"$root/f") { fos =>
          using(new ObjectOutputStream(fos))(oos => oos.writeObject(f))
        }
      }
    )

    val uploadContexts = executor.submit[Unit](() =>
      retryTransientErrors {
        fs.writePDOS(s"$root/contexts") { os =>
          var o = 12L * n
          collection.foreach { context =>
            val len = context.length
            os.writeLong(o)
            os.writeInt(len)
            o += len
          }
          collection.foreach(context => os.write(context))
        }
      }
    )

    uploadFunction.get()
    uploadContexts.get()

    val jobs = collection.zipWithIndex.map { case (_, i) =>
      var resources = JObject("preemptible" -> JBool(true))
      if (backendContext.workerCores != "None") {
        resources = resources.merge(JObject("cpu" -> JString(backendContext.workerCores)))
      }
      if (backendContext.workerMemory != "None") {
        resources = resources.merge(JObject("memory" -> JString(backendContext.workerMemory)))
      }
      if (backendContext.storageRequirement != "0Gi") {
        resources =
          resources.merge(JObject("storage" -> JString(backendContext.storageRequirement)))
      }

      val command = sys.env.get("HAIL_QOB_WARM_JVM") match {
        case Some(_) => Array("java", "-XX:CRaCRestoreFrom=/opt/crac-files")
        case None => Array(
            "/bin/bash",
            "-c",
            "java -cp hail.jar:$SPARK_HOME/jars/* is.hail.backend.service.Worker",
          )
      }

      JObject(
        "always_run" -> JBool(false),
        "job_id" -> JInt(i + 1),
        "in_update_parent_ids" -> JArray(List()),
        "process" -> JObject(
          "command" -> JArray(command.map(JString(_)).toList),
          "type" -> JString("docker"),
          // "profile" -> JBool(backendContext.profile),
          "image" -> JString(image),
        ),
        "env" -> JArray(List(
          JObject("name" -> JString("HAIL_CLOUD"), "value" -> JString(System.getenv("HAIL_CLOUD"))),
          JObject("name" -> JString("HAIL_QOB_KIND"), "value" -> JString(Main.WORKER)),
          JObject("name" -> JString("ROOT"), "value" -> JString(root)),
          JObject("name" -> JString("I"), "value" -> JString(s"$i")),
          JObject("name" -> JString("N"), "value" -> JString(s"$n")),
        )),
        "attributes" -> JObject(
          "name" -> JString(s"${name}_stage${stageCount}_${stageIdentifier}_job$i")
        ),
        "mount_tokens" -> JBool(true),
        "resources" -> resources,
        "regions" -> JArray(backendContext.regions.map(JString).toList),
        "cloudfuse" -> JArray(backendContext.cloudfuseConfig.map { config =>
          JObject(
            "bucket" -> JString(config.bucket),
            "mount_path" -> JString(config.mount_path),
            "read_only" -> JBool(config.read_only),
          )
        }.toList),
      )
    }

    log.info(s"parallelizeAndComputeWithIndex: $token: running job")

    val (batchId, updateId) = curBatchId match {
      case Some(id) =>
        (id, batchClient.update(id, token, jobs))
      case None =>
        val batchId = batchClient.create(
          JObject(
            "billing_project" -> JString(backendContext.billingProject),
            "n_jobs" -> JInt(n),
            "token" -> JString(token),
            "attributes" -> JObject("name" -> JString(name + "_" + stageCount)),
          ),
          jobs,
        )
        (batchId, 1L)
    }

    val batch = batchClient.waitForBatch(batchId, true)

    stageCount += 1
    implicit val formats: Formats = DefaultFormats
    val batchState = (batch \ "state").extract[String]
    if (batchState == "failed") {
      throw new HailBatchFailure(s"Update $updateId for batch $batchId failed")
    }

    (token, root, n)
  }

  private[this] def readResult(root: String, i: Int): Array[Byte] = {
    val bytes = fs.readNoCompression(s"$root/result.$i")
    if (bytes(0) != 0) {
      bytes.slice(1, bytes.length)
    } else {
      val errorInformationBytes = bytes.slice(1, bytes.length)
      val is = new DataInputStream(new ByteArrayInputStream(errorInformationBytes))
      val shortMessage = readString(is)
      val expandedMessage = readString(is)
      val errorId = is.readInt()
      throw new HailWorkerException(i, shortMessage, expandedMessage, errorId)
    }
  }

  override def parallelizeAndComputeWithIndex(
    _backendContext: BackendContext,
    fs: FS,
    collection: Array[Array[Byte]],
    stageIdentifier: String,
    dependency: Option[TableStageDependency] = None,
  )(
    f: (Array[Byte], HailTaskContext, HailClassLoader, FS) => Array[Byte]
  ): Array[Array[Byte]] = {
    val (token, root, n) =
      submitAndWaitForBatch(_backendContext, fs, collection, stageIdentifier, f)

    log.info(s"parallelizeAndComputeWithIndex: $token: reading results")
    val startTime = System.nanoTime()
    val results =
      try
        executor.invokeAll[Array[Byte]](
          IndexedSeq.range(0, n).map { i =>
            (() => readResult(root, i)): Callable[Array[Byte]]
          }.asJavaCollection
        ).asScala.map(_.get).toArray
      catch {
        case exc: ExecutionException if exc.getCause() != null => throw exc.getCause()
      }
    val resultsReadingSeconds = (System.nanoTime() - startTime) / 1000000000.0
    val rate = results.length / resultsReadingSeconds
    val byterate = results.map(_.length).sum / resultsReadingSeconds / 1024 / 1024
    log.info(s"all results read. $resultsReadingSeconds s. $rate result/s. $byterate MiB/s.")
    results
  }

  override def parallelizeAndComputeWithIndexReturnAllErrors(
    _backendContext: BackendContext,
    fs: FS,
    collection: IndexedSeq[(Array[Byte], Int)],
    stageIdentifier: String,
    dependency: Option[TableStageDependency] = None,
  )(
    f: (Array[Byte], HailTaskContext, HailClassLoader, FS) => Array[Byte]
  ): (Option[Throwable], IndexedSeq[(Array[Byte], Int)]) = {
    val (token, root, _) =
      submitAndWaitForBatch(_backendContext, fs, collection.map(_._1).toArray, stageIdentifier, f)
    log.info(s"parallelizeAndComputeWithIndex: $token: reading results")
    val startTime = System.nanoTime()
    val r @ (_, results) = runAllKeepFirstError(executor) {
      collection.zipWithIndex.map { case ((_, i), jobIndex) =>
        (() => readResult(root, jobIndex), i)
      }
    }
    val resultsReadingSeconds = (System.nanoTime() - startTime) / 1000000000.0
    val rate = results.length / resultsReadingSeconds
    val byterate = results.map(_._1.length).sum / resultsReadingSeconds / 1024 / 1024
    log.info(s"all results read. $resultsReadingSeconds s. $rate result/s. $byterate MiB/s.")
    r
  }

  def stop(): Unit =
    executor.shutdownNow()

  private[this] def execute(ctx: ExecuteContext, _x: IR, bufferSpecString: String): Array[Byte] = {
    TypeCheck(ctx, _x)
    Validate(_x)
    val x = LoweringPipeline.darrayLowerer(true)(DArrayLowering.All).apply(ctx, _x)
      .asInstanceOf[IR]
    if (x.typ == TVoid) {
      val (_, f) = Compile[AsmFunction1RegionUnit](
        ctx,
        FastSeq(),
        FastSeq[TypeInfo[_]](classInfo[Region]),
        UnitInfo,
        x,
        optimize = true,
      )

      ctx.scopedExecution((hcl, fs, htc, r) => f(hcl, fs, htc, r).apply(r))
      Array()
    } else {
      val (Some(PTypeReferenceSingleCodeType(pt: PTuple)), f) = Compile[AsmFunction1RegionLong](
        ctx,
        FastSeq(),
        FastSeq(classInfo[Region]),
        LongInfo,
        MakeTuple.ordered(FastSeq(x)),
        optimize = true,
      )
      val elementType = pt.fields(0).typ
      val off = ctx.scopedExecution((hcl, fs, htc, r) => f(hcl, fs, htc, r).apply(r))
      val codec = TypedCodecSpec(
        EType.fromPythonTypeEncoding(elementType.virtualType),
        elementType.virtualType,
        BufferSpec.parseOrDefault(bufferSpecString),
      )
      assert(pt.isFieldDefined(off, 0))
      codec.encode(ctx, elementType, pt.loadField(off, 0))
    }
  }

  def execute(
    ctx: ExecuteContext,
    code: String,
    token: String,
    bufferSpecString: String,
  ): Array[Byte] = {
    log.info(s"executing: $token ${ctx.fs.getConfiguration()}")
    val ir = IRParser.parse_value_ir(ctx, code)
    ctx.irMetadata = ctx.irMetadata.copy(semhash = SemanticHash(ctx)(ir))
    execute(ctx, ir, bufferSpecString)
  }

  override def lowerDistributedSort(
    ctx: ExecuteContext,
    inputStage: TableStage,
    sortFields: IndexedSeq[SortField],
    rt: RTable,
    nPartitions: Option[Int],
  ): TableReader =
    LowerDistributedSort.distributedSort(ctx, inputStage, sortFields, rt, nPartitions)

  def persist(backendContext: BackendContext, id: String, value: BlockMatrix, storageLevel: String)
    : Unit = ???

  def unpersist(backendContext: BackendContext, id: String): Unit = ???

  def getPersistedBlockMatrix(backendContext: BackendContext, id: String): BlockMatrix = ???

  def getPersistedBlockMatrixType(backendContext: BackendContext, id: String): BlockMatrixType = ???

  def tableToTableStage(ctx: ExecuteContext, inputIR: TableIR, analyses: LoweringAnalyses)
    : TableStage =
    LowerTableIR.applyTable(inputIR, DArrayLowering.All, ctx, analyses)

  override def withExecuteContext[T](methodName: String)(f: ExecuteContext => T): T =
    ExecutionTimer.logTime(methodName) { timer =>
      ExecuteContext.scoped(
        tmpdir,
        "file:///tmp",
        this,
        fs,
        timer,
        null,
        theHailClassLoader,
        flags,
        serviceBackendContext,
      )(f)
    }

  def addLiftover(name: String, chainFile: String, destRGName: String): Unit =
    withExecuteContext("addLiftover") { ctx =>
      references(name).addLiftover(ctx, chainFile, destRGName)
    }

  def addSequence(name: String, fastaFile: String, indexFile: String): Unit =
    withExecuteContext("addSequence") { ctx =>
      references(name).addSequence(ctx, fastaFile, indexFile)
    }
}

class EndOfInputException extends RuntimeException
class HailBatchFailure(message: String) extends RuntimeException(message)

object ServiceBackendAPI {
  private[this] val log = Logger.getLogger(getClass.getName())

  def main(argv: Array[String]): Unit = {
    val name = System.getenv("NAME")
    val image = System.getenv("HAIL_QOB_IMAGE")
    // val inputURL = System.getenv("INPUT_URL")

    // sys.env.get("HAIL_SSL_CONFIG_DIR").foreach(tls.setSSLConfigFromDir(_))
    val batchClient = new BatchClient()

    val batchId = sys.env.get("HAIL_BATCH_ID").map(_.toLong)
    log.info("Retrieved HAIL_BATCH_ID")

    implicit val formats: Formats = DefaultFormats
    val input = sys.env.get("INPUT_URL") match {
      case Some(inputURL) =>
        val fs = FS.cloudSpecificFS(None)
        using(fs.openNoCompression(inputURL))(JsonMethods.parse(_))
      case None => JsonMethods.parse(System.in)
    }
    log.info("Read input")
    val rpcConfig = (input \ "config").extract[ServiceBackendRPCPayload]
    log.info("Extracted RPC config")

    val backend = ServiceBackend(
      image,
      name,
      new HailClassLoader(getClass().getClassLoader()),
      batchClient,
      batchId,
      rpcConfig,
    )
    log.info("ServiceBackend allocated.")
    if (HailContext.isInitialized) {
      HailContext.get.backend = backend
      log.info("Default references added to already initialized HailContext.")
    } else {
      HailContext(backend, 50, 3)
      log.info("HailContexet initialized.")
    }

    val action = (input \ "action").extract[Int]
    val payload = (input \ "payload")
    new ServiceBackendAPI(backend).executeOneCommand(action, payload)

    HailContext.stop()
  }
}

private class HailSocketAPIOutputStream(
  private[this] val out: OutputStream
) extends AutoCloseable {
  private[this] var closed: Boolean = false
  private[this] val dummy = new Array[Byte](8)

  def writeBool(b: Boolean): Unit =
    out.write(if (b) 1 else 0)

  def writeInt(v: Int): Unit = {
    Memory.storeInt(dummy, 0, v)
    out.write(dummy, 0, 4)
  }

  def writeLong(v: Long): Unit = {
    Memory.storeLong(dummy, 0, v)
    out.write(dummy)
  }

  def writeBytes(bytes: Array[Byte]): Unit = {
    writeInt(bytes.length)
    out.write(bytes)
  }

  def writeString(s: String): Unit = writeBytes(s.getBytes(StandardCharsets.UTF_8))

  def close(): Unit =
    if (!closed) {
      out.close()
      closed = true
    }
}

case class CloudfuseConfig(bucket: String, mount_path: String, read_only: Boolean)

case class SequenceConfig(fasta: String, index: String)

case class ServiceBackendRPCPayload(
  tmp_dir: String,
  remote_tmpdir: String,
  billing_project: String,
  worker_cores: String,
  worker_memory: String,
  storage: String,
  cloudfuse_configs: Array[CloudfuseConfig],
  regions: Array[String],
  flags: Map[String, String],
  custom_references: Array[String],
  liftovers: Map[String, Map[String, String]],
  sequences: Map[String, SequenceConfig],
)

case class ServiceBackendExecutePayload(
  functions: Array[SerializedIRFunction],
  idempotency_token: String,
  payload: ExecutePayload,
)

case class SerializedIRFunction(
  name: String,
  type_parameters: Array[String],
  value_parameter_names: Array[String],
  value_parameter_types: Array[String],
  return_type: String,
  rendered_body: String,
)

class ServiceBackendAPI(
  private[this] val backend: ServiceBackend
) extends Thread {
  private[this] val LOAD_REFERENCES_FROM_DATASET = 1
  private[this] val VALUE_TYPE = 2
  private[this] val TABLE_TYPE = 3
  private[this] val MATRIX_TABLE_TYPE = 4
  private[this] val BLOCK_MATRIX_TYPE = 5
  private[this] val EXECUTE = 6
  private[this] val PARSE_VCF_METADATA = 7
  private[this] val IMPORT_FAM = 8
  private[this] val FROM_FASTA_FILE = 9

  private[this] val log = Logger.getLogger(getClass.getName())

  private[this] def doAction(action: Int, payload: JValue): Array[Byte] = retryTransientErrors {
    implicit val formats: Formats = DefaultFormats
    (action: @switch) match {
      case LOAD_REFERENCES_FROM_DATASET =>
        val path = payload.extract[LoadReferencesFromDatasetPayload].path
        backend.loadReferencesFromDataset(path)
      case VALUE_TYPE =>
        val ir = payload.extract[IRTypePayload].ir
        backend.valueType(ir)
      case TABLE_TYPE =>
        val ir = payload.extract[IRTypePayload].ir
        backend.tableType(ir)
      case MATRIX_TABLE_TYPE =>
        val ir = payload.extract[IRTypePayload].ir
        backend.matrixTableType(ir)
      case BLOCK_MATRIX_TYPE =>
        val ir = payload.extract[IRTypePayload].ir
        backend.blockMatrixType(ir)
      case EXECUTE =>
        val qobExecutePayload = payload.extract[ServiceBackendExecutePayload]
        val bufferSpecString = qobExecutePayload.payload.stream_codec
        val code = qobExecutePayload.payload.ir
        val token = qobExecutePayload.idempotency_token
        backend.withExecuteContext("ServiceBackend.execute") { ctx =>
          withIRFunctionsReadFromInput(qobExecutePayload.functions, ctx) { () =>
            backend.execute(ctx, code, token, bufferSpecString)
          }
        }
      case PARSE_VCF_METADATA =>
        val path = payload.extract[ParseVCFMetadataPayload].path
        backend.parseVCFMetadata(path)
      case IMPORT_FAM =>
        val famPayload = payload.extract[ImportFamPayload]
        val path = famPayload.path
        val quantPheno = famPayload.quant_pheno
        val delimiter = famPayload.delimiter
        val missing = famPayload.missing
        backend.importFam(path, quantPheno, delimiter, missing)
      case FROM_FASTA_FILE =>
        val fastaPayload = payload.extract[FromFASTAFilePayload]
        backend.fromFASTAFile(
          fastaPayload.name,
          fastaPayload.fasta_file,
          fastaPayload.index_file,
          fastaPayload.x_contigs,
          fastaPayload.y_contigs,
          fastaPayload.mt_contigs,
          fastaPayload.par,
        )
    }
  }

  private[this] def withIRFunctionsReadFromInput(
    serializedFunctions: Array[SerializedIRFunction],
    ctx: ExecuteContext,
  )(
    body: () => Array[Byte]
  ): Array[Byte] = {
    try {
      serializedFunctions.foreach { func =>
        IRFunctionRegistry.pyRegisterIRForServiceBackend(
          ctx,
          func.name,
          func.type_parameters,
          func.value_parameter_names,
          func.value_parameter_types,
          func.return_type,
          func.rendered_body,
        )
      }
      body()
    } finally
      IRFunctionRegistry.clearUserFunctions()
  }

  private def writeOutput(write: HailSocketAPIOutputStream => Unit): Unit = {
    val os = sys.env.get("OUTPUT_URL") match {
      case Some(outputURL) => backend.fs.createNoCompression(outputURL)
      case None => System.out
    }
    retryTransientErrors {
      using(os)(output => write(new HailSocketAPIOutputStream(output)))
    }
  }

  def executeOneCommand(action: Int, payload: JValue): Unit = {
    try {
      val result = doAction(action, payload)
      writeOutput { output =>
        output.writeBool(true)
        output.writeBytes(result)
      }
    } catch {
      case exc: HailWorkerException =>
        writeOutput { output =>
          output.writeBool(false)
          output.writeString(exc.shortMessage)
          output.writeString(exc.expandedMessage)
          output.writeInt(exc.errorId)
        }
        log.error(
          "A worker failed. The exception was written for Python but we will also throw an exception to fail this driver job."
        )
        throw exc
      case t: Throwable =>
        val (shortMessage, expandedMessage, errorId) = handleForPython(t)
        writeOutput { output =>
          output.writeBool(false)
          output.writeString(shortMessage)
          output.writeString(expandedMessage)
          output.writeInt(errorId)
        }
        log.error(
          "An exception occurred in the driver. The exception was written for Python but we will re-throw to fail this driver job."
        )
        throw t
    }
  }
}
