package is.hail.backend.service

import is.hail.HailContext
import is.hail.backend.local.LocalBackend

import org.crac.Core

object Main {
  val WORKER = "worker"
  val DRIVER = "driver"

  def main(argv: Array[String]): Unit = {
    HailContext.configureStderrLogging()

    LocalBackend.warmUp()
    Core.checkpointRestore()

    sys.env.get("HAIL_QOB_KIND") match {
      case Some(WORKER) => Worker.main(argv)
      case Some(DRIVER) => ServiceBackendAPI.main(argv)
      case kind => throw new RuntimeException(s"unknown kind: $kind")
    }
  }
}
