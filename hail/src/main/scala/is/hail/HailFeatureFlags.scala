package is.hail

import is.hail.backend.ExecutionCache
import is.hail.utils._
import org.json4s.JsonAST.{JArray, JObject, JString}

import scala.collection.mutable

object HailFeatureFlags {
  val defaults = Map[String, (String, String)](
    // Must match __flags_env_vars_and_defaults in hail/backend/backend.py
    //
    // The default values and envvars here are only used in the Scala tests. In all other
    // conditions, Python initializes the flags, see HailContext._initialize_flags in context.py.
  )

  def fromEnv(): HailFeatureFlags = {
    var flags = mutable.Map[String, String]()
    flags("no_whole_stage_codegen") = null
    flags("no_ir_logging") = null
    flags("lower") = null
    flags("lower_only") = null
    flags("lower_bm") = null
    flags("print_ir_on_worker") = null
    flags("print_inputs_on_worker") = null
    flags("max_leader_scans") = "1000"
    flags("distributed_scan_comb_op") = null
    flags("jvm_bytecode_dump") = null
    flags("write_ir_files") = null
    flags("method_split_ir_limit") = "16"
    flags("use_new_shuffle") = null
    flags("shuffle_max_branch_factor") = "64"
    flags("shuffle_cutoff_to_local_sort") = "512000000"
    flags("grouped_aggregate_buffer_size") = "50"
    flags("use_ssa_logs") = "1"
    flags("gcs_requester_pays_project") = null
    flags("gcs_requester_pays_buckets") = null
    flags("index_branching_factor") = null
    flags("rng_nonce") = "0x0"
    flags("profile") = null
    flags(ExecutionCache.Flags.UseFastRestarts) = null
    flags(ExecutionCache.Flags.Cachedir) = null
    new HailFeatureFlags(flags)
  }

  def fromMap(m: Map[String, String]): HailFeatureFlags =
    new HailFeatureFlags(
      mutable.Map(
        HailFeatureFlags.defaults.map {
          case (flagName, (_, default)) => (flagName, m.getOrElse(flagName, default))
        }.toFastSeq: _*
      )
    )
}

class HailFeatureFlags private (
  val flags: mutable.Map[String, String]
) extends Serializable {
  val available: java.util.ArrayList[String] =
    new java.util.ArrayList[String](java.util.Arrays.asList[String](flags.keys.toSeq: _*))

  def set(flag: String, value: String): Unit = {
    assert(exists(flag))
    flags.update(flag, value)
  }

  def get(flag: String): String = flags(flag)

  def exists(flag: String): Boolean = flags.contains(flag)

  def toJSONEnv: JArray =
    JArray(flags.filter { case (_, v) =>
      v != null
    }.map{ case (name, v) =>
      JObject(
        "name" -> JString(HailFeatureFlags.defaults(name)._1),
        "value" -> JString(v))
    }.toList)
}
