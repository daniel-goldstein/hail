package is.hail.expr.ir

import is.hail.backend.ExecuteContext
import is.hail.io.fs.FS
import is.hail.rvd._
import is.hail.types._
import is.hail.types.physical.PStruct
import is.hail.types.virtual._
import is.hail.utils._
import is.hail.variant.ReferenceGenome
import org.json4s._
import org.json4s.jackson.JsonMethods
import org.json4s.jackson.JsonMethods.parse

import java.io.OutputStreamWriter
import scala.collection.mutable
import scala.language.{existentials, implicitConversions}

abstract class ComponentSpec

object RelationalSpec {
  implicit val formats: Formats = new DefaultFormats() {
    override val typeHints = ShortTypeHints(List(
      classOf[ComponentSpec], classOf[RVDComponentSpec], classOf[PartitionCountsComponentSpec], classOf[PropertiesSpec],
      classOf[RelationalSpec], classOf[MatrixTableSpec], classOf[TableSpec]), typeHintFieldName="name")
  } +
    new TableTypeSerializer +
    new MatrixTypeSerializer

  def readMetadata(fs: FS, path: String): JValue = {
    if (!fs.isDir(path)) {
      if (!fs.exists(path)) {
        fatal(s"No file or directory found at ${path}")
      } else {
        fatal(s"MatrixTable and Table files are directories; path '$path' is not a directory")
      }
    }
    val metadataFile = path + "/metadata.json.gz"
    val jv = using(fs.open(metadataFile)) { in => parse(in) }

    val fileVersion = jv \ "file_version" match {
      case JInt(rep) => SemanticVersion(rep.toInt)
      case _ =>
        fatal(
          s"""cannot read file: metadata does not contain file version: $metadataFile
             |  Common causes:
             |    - File is an 0.1 VariantDataset or KeyTable (0.1 and 0.2 native formats are not compatible!)""".stripMargin)
    }

    if (!FileFormat.version.supports(fileVersion))
      fatal(s"incompatible file format when reading: $path\n  supported file format version: ${ FileFormat.version }, found file format version $fileVersion" +
        s"\n  The cause of this error is usually an attempt to use an older version of Hail to read files " +
        s"generated by a newer version. This is not supported (Hail native files are back-compatible, but not forward-compatible)." +
        s"\n  To read this file, use a newer version of Hail. Note that the file format version and the Hail Python library version are not the same.")
    jv
  }

  def read(fs: FS, path: String): RelationalSpec = {
    val jv = readMetadata(fs, path)
    val references = readReferences(fs, path, jv)

    (jv \ "name").extract[String] match {
      case "TableSpec" => TableSpec.fromJValue(fs, path, jv)
      case "MatrixTableSpec" => MatrixTableSpec.fromJValue(fs, path, jv)
    }
  }

  def readReferences(fs: FS, path: String): Array[ReferenceGenome] =
    readReferences(fs, path, readMetadata(fs, path))

  def readReferences(fs: FS, path: String, jv: JValue): Array[ReferenceGenome] = {
    // FIXME this violates the abstraction of the serialization boundary
    val referencesRelPath = (jv \ "references_rel_path").extract[String]
    ReferenceGenome.readReferences(fs, path + "/" + referencesRelPath)
  }
}

abstract class RelationalSpec {
  def file_version: Int

  def hail_version: String

  def components: Map[String, ComponentSpec]

  def getComponent[T <: ComponentSpec](name: String): T = components(name).asInstanceOf[T]

  def getOptionalComponent[T <: ComponentSpec](name: String): Option[T] = components.get(name).map(_.asInstanceOf[T])

  def globalsComponent: RVDComponentSpec = getComponent[RVDComponentSpec]("globals")

  def partitionCounts: Array[Long] = getComponent[PartitionCountsComponentSpec]("partition_counts").counts.toArray

  def isDistinctlyKeyed: Boolean = getOptionalComponent[PropertiesSpec]("properties").flatMap(_.properties.values.get("distinctlyKeyed").map(_.asInstanceOf[Boolean])).getOrElse(false)

  def indexed: Boolean

  def version: SemanticVersion = SemanticVersion(file_version)

  def toJValue: JValue
}

case class RVDComponentSpec(rel_path: String) extends ComponentSpec {
  def absolutePath(path: String): String = path + "/" + rel_path

  private[this] val specCache = mutable.Map.empty[String, AbstractRVDSpec]
  def rvdSpec(fs: FS, path: String): AbstractRVDSpec = {
    specCache.getOrElseUpdate(path, AbstractRVDSpec.read(fs, absolutePath(path)))
  }

  def indexed(fs: FS, path: String): Boolean = rvdSpec(fs, path).indexed
}

case class PartitionCountsComponentSpec(counts: Seq[Long]) extends ComponentSpec

case class PropertiesSpec(properties: JObject) extends ComponentSpec

abstract class AbstractMatrixTableSpec extends RelationalSpec {
  def matrix_type: MatrixType

  def references_rel_path: String

  def colsComponent: RVDComponentSpec = getComponent[RVDComponentSpec]("cols")

  def rowsComponent: RVDComponentSpec = getComponent[RVDComponentSpec]("rows")

  def entriesComponent: RVDComponentSpec = getComponent[RVDComponentSpec]("entries")

  def globalsSpec: AbstractTableSpec

  def colsSpec: AbstractTableSpec

  def rowsSpec: AbstractTableSpec

  def entriesSpec: AbstractTableSpec

  def indexed: Boolean = rowsSpec.indexed
}

object MatrixTableSpec {
  def fromJValue(fs: FS, path: String, jv: JValue): MatrixTableSpec = {
    implicit val formats: Formats = new DefaultFormats() {
      override val typeHints = ShortTypeHints(List(
        classOf[ComponentSpec], classOf[RVDComponentSpec], classOf[PartitionCountsComponentSpec]), typeHintFieldName = "name")
    } +
      new MatrixTypeSerializer
    val params = jv.extract[MatrixTableSpecParameters]

    val globalsSpec = RelationalSpec.read(fs, path + "/globals").asInstanceOf[AbstractTableSpec]

    val colsSpec = RelationalSpec.read(fs, path + "/cols").asInstanceOf[AbstractTableSpec]

    val rowsSpec = RelationalSpec.read(fs, path + "/rows").asInstanceOf[AbstractTableSpec]

    // some legacy files written as MatrixTableSpec wrote the wrong type to the entries table metadata
    var entriesSpec = RelationalSpec.read(fs, path + "/entries").asInstanceOf[TableSpec]
    entriesSpec = TableSpec(fs, path + "/entries",
      entriesSpec.params.copy(
        table_type = TableType(params.matrix_type.entriesRVType, FastIndexedSeq(), params.matrix_type.globalType)))

    new MatrixTableSpec(params, globalsSpec, colsSpec, rowsSpec, entriesSpec)
  }
}

case class MatrixTableSpecParameters(
  file_version: Int,
  hail_version: String,
  references_rel_path: String,
  matrix_type: MatrixType,
  components: Map[String, ComponentSpec]) {

  def write(fs: FS, path: String) {
    using(new OutputStreamWriter(fs.create(path + "/metadata.json.gz"))) { out =>
      out.write(JsonMethods.compact(decomposeWithName(this, "MatrixTableSpec")(RelationalSpec.formats)))
    }
  }

}

class MatrixTableSpec(
  val params: MatrixTableSpecParameters,
  val globalsSpec: AbstractTableSpec,
  val colsSpec: AbstractTableSpec,
  val rowsSpec: AbstractTableSpec,
  val entriesSpec: AbstractTableSpec) extends AbstractMatrixTableSpec {
  def references_rel_path: String = params.references_rel_path

  def file_version: Int = params.file_version

  def hail_version: String = params.hail_version

  def matrix_type: MatrixType = params.matrix_type

  def components: Map[String, ComponentSpec] = params.components

  def toJValue: JValue = {
    decomposeWithName(params, "MatrixTableSpec")(RelationalSpec.formats)
  }
}

object FileFormat {
  val version: SemanticVersion = SemanticVersion(1, 7, 0)
}
