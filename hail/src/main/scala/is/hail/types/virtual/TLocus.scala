package is.hail.types.virtual

import is.hail.annotations._
import is.hail.backend.{BroadcastValue, HailStateManager}
import is.hail.check._
import is.hail.types.physical.PLocus
import is.hail.types.virtual.TCall.representation
import is.hail.utils._
import is.hail.variant._

import scala.reflect.{ClassTag, classTag}

object TLocus {
  val representation: TStruct = {
    TStruct(
      "contig" -> TString,
      "position" -> TInt32)
  }

  def schemaFromRG(rg: Option[String], required: Boolean = false): Type = rg match {
    // must match tlocus.schema_from_rg
    case Some(name) => TLocus(name)
    case None => TLocus.representation
  }
}

case class TLocus(rgName: String) extends Type {

  def _toPretty = s"Locus($rgName)"

  def rg: String = rgName

  override def pyString(sb: StringBuilder): Unit = {
    sb.append("locus<")
    sb.append(prettyIdentifier(rgName))
    sb.append('>')
  }
  def _typeCheck(a: Any): Boolean = a.isInstanceOf[Locus]

  override def genNonmissingValue(sm: HailStateManager): Gen[Annotation] = Locus.gen(sm.referenceGenomes(rgName))

  override def scalaClassTag: ClassTag[Locus] = classTag[Locus]

  override def mkOrdering(sm: HailStateManager, missingEqual: Boolean = true): ExtendedOrdering =
    ExtendedOrdering.extendToNull(sm.referenceGenomes(rgName).locusOrdering, missingEqual)

  override def mkOrdering(rg: ReferenceGenome, missingEqual: Boolean = true): ExtendedOrdering =
    ExtendedOrdering.extendToNull(rg.locusOrdering, missingEqual)

  lazy val representation: TStruct = TLocus.representation

  def locusOrdering(sm: HailStateManager): Ordering[Locus] = sm.referenceGenomes(rgName).locusOrdering

  override def unify(concrete: Type): Boolean = concrete match {
    case TLocus(crgName) => rgName == crgName
    case _ => false
  }
}
