use actix_web::middleware::Logger;
use actix_web::{get, post, web, App, HttpRequest, HttpResponse, HttpServer, Responder};
use openssl::ssl::{SslAcceptor, SslAcceptorBuilder, SslFiletype, SslMethod};
use serde::Deserialize;
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

type InstancesMcpuMap = Mutex<HashMap<String, i32>>;

type Db = mysql_async::Pool;

use mysql_async;
use mysql_async::prelude::*;

#[allow(dead_code)]
#[derive(Deserialize)]
struct SslConfig {
    outgoing_trust: String,
    outgoing_trust_store: String,
    incoming_trust: String,
    incoming_trust_store: String,
    key: String,
    cert: String,
    key_store: String,
}

fn load_ssl_config() -> SslConfig {
    let ssl_config_file =
        fs::read_to_string("/ssl-config/ssl-config.json").expect("No SSL config found");
    serde_json::from_str(&ssl_config_file).expect("SSL Config malformed")
}

fn build_client() -> reqwest::Client {
    let ssl_config = load_ssl_config();
    let ca_pem = fs::read_to_string("/ssl-config/".to_string() + &ssl_config.cert).unwrap();
    let ca_cert = reqwest::Certificate::from_pem(ca_pem.as_bytes()).unwrap();
    reqwest::Client::builder()
        .add_root_certificate(ca_cert)
        .danger_accept_invalid_certs(true)
        .build()
        .unwrap()
}

fn build_ssl_acceptor_builder() -> SslAcceptorBuilder {
    let ssl_config = load_ssl_config();
    let mut builder = SslAcceptor::mozilla_intermediate(SslMethod::tls()).unwrap();
    builder
        .set_private_key_file(
            "/ssl-config/".to_string() + &ssl_config.key,
            SslFiletype::PEM,
        )
        .unwrap();
    builder
        .set_certificate_chain_file("/ssl-config/".to_string() + &ssl_config.cert)
        .unwrap();

    builder
}

#[allow(dead_code)]
#[derive(Deserialize)]
#[serde(rename_all = "kebab-case")]
struct SqlConfig {
    host: String,
    port: u32,
    db: Option<String>,
    user: String,
    password: String,
    instance: Option<String>,
    connection_name: Option<String>,
    ssl_ca: String,
    ssl_cert: String,
    ssl_key: String,
    client_pkcs: String,
    ssl_mode: String,
}

fn build_sql_config() -> mysql_async::Opts {
    let sql_config_file =
        fs::read_to_string("/sql-config/sql-config.json").expect("No SQL config found");
    let sql_config: SqlConfig =
        serde_json::from_str(&sql_config_file).expect("SQL Config malformed");

    let ssl_opts = mysql_async::SslOpts::default()
        .with_root_cert_path(Some(PathBuf::from(sql_config.ssl_ca.clone())))
        .with_pkcs12_path(Some(PathBuf::from(sql_config.client_pkcs.clone())))
        .with_password(Some("password"))
        .with_danger_accept_invalid_certs(true);

    mysql_async::OptsBuilder::default()
        .ip_or_hostname(sql_config.host)
        .db_name(sql_config.db)
        .user(Some(sql_config.user))
        .pass(Some(sql_config.password))
        .ssl_opts(ssl_opts)
        .into()
}

fn instance_name_from_request(req: &HttpRequest) -> &str {
    req.headers()
        .get("X-Hail-Instance-Name")
        .unwrap()
        .to_str()
        .unwrap()
}

#[get("/instances")]
async fn greet(instances_map: web::Data<InstancesMcpuMap>) -> impl Responder {
    let map = {
        // Not sure if we need this additional scope
        // but what I'm trying to do is make sure we release the lock as
        // soon as possible, i.e. after cloning
        instances_map.lock().unwrap().clone()
    };

    web::Json(map)
}

fn update_instances(instances_map: &InstancesMcpuMap, instance_name: &str, delta_cores_mcpu: i32) {
    if delta_cores_mcpu != 0 {
        let mut map = instances_map.lock().unwrap();
        let mcpu = map.get(instance_name).unwrap_or(&0).clone();
        map.insert(instance_name.to_string(), delta_cores_mcpu + mcpu);
    }
}

async fn send_instance_updates(client: &reqwest::Client, instances_map: &InstancesMcpuMap) {
    let instances_mcpu = {
        let mut map = instances_map.lock().unwrap();
        let copy = map.clone();
        map.clear();
        copy
    };
    if !instances_mcpu.is_empty() {
        let data = HashMap::from([("open_cores", instances_mcpu)]);
        let res = client
            .post(
                "https://batch-driver.dgoldste/dgoldste/batch-driver/api/v1alpha/instances/adjust_cores",
            )
            .json(&data)
            .send()
            .await;
        if let Err(e) = res {
            eprintln!("Failed: {}", e);
        }
    }
}

#[allow(dead_code)]
#[derive(Deserialize)]
struct MJSRequestStatus {
    version: u32,
    batch_id: u64,
    job_id: u64,
    attempt_id: String,
    start_time: u64,
    resources: Vec<Resource>,
}

#[derive(Deserialize)]
struct MJSRequest {
    status: MJSRequestStatus,
}

#[post("/dgoldste/batch-db-proxy/api/v1alpha/instances/job_started")]
async fn job_started(
    req: HttpRequest,
    body: web::Json<MJSRequest>,
    instances_map: web::Data<InstancesMcpuMap>,
    db: web::Data<Db>,
) -> impl Responder {
    let status = &body.status;
    let instance_name = instance_name_from_request(&req);

    let delta_cores_mcpu = mark_job_started(
        &db,
        status.batch_id,
        status.job_id,
        &status.attempt_id,
        &instance_name,
        status.start_time,
        &status.resources,
    )
    .await
    .unwrap();

    update_instances(&instances_map, &instance_name, delta_cores_mcpu);
    HttpResponse::Ok()
}

#[allow(dead_code)]
#[derive(Deserialize)]
struct Resource {
    name: String,
    quantity: u64,
}

#[allow(dead_code)]
#[derive(Deserialize)]
struct MJCRequestStatus {
    version: u32,
    batch_id: u64,
    job_id: u64,
    attempt_id: String,
    state: String,
    start_time: u64,
    end_time: u64,
    status: (u64, u64),
    resources: Vec<Resource>,
}

#[derive(Deserialize)]
struct MJCRequest {
    status: MJCRequestStatus,
}

#[post("/dgoldste/batch-db-proxy/api/v1alpha/instances/job_complete")]
async fn job_complete(
    req: HttpRequest,
    body: web::Json<MJCRequest>,
    instances_map: web::Data<InstancesMcpuMap>,
    db: web::Data<Db>,
) -> impl Responder {
    let status = &body.status;
    let new_state = match status.state.as_str() {
        "succeeded" => "Success",
        "error" => "Error",
        "failed" => "Failed",
        _ => panic!("Unexpected state: {}", status.state),
    };
    let instance_name = instance_name_from_request(&req);
    let delta_cores_mcpu = mark_job_complete(
        &db,
        status.batch_id,
        status.job_id,
        &status.attempt_id,
        instance_name,
        new_state,
        &status.status,
        status.start_time,
        status.end_time,
        "completed",
        &status.resources,
    )
    .await
    .unwrap();

    update_instances(&instances_map, &instance_name, delta_cores_mcpu);
    HttpResponse::Ok()
}

async fn mark_job_started(
    db: &Db,
    batch_id: u64,
    job_id: u64,
    attempt_id: &str,
    instance_name: &str,
    start_time: u64,
    resources: &Vec<Resource>,
) -> Result<i32, Box<dyn std::error::Error>> {
    let mut conn = db.get_conn().await?;
    let (_rc, delta_cores_mcpu): (i32, i32) =
        r"CALL mark_job_started(:batch_id, :job_id, :attempt_id, :instance_name, :state_time);"
            .with(params! {
                "batch_id" => batch_id,
                "job_id" => job_id,
                "attempt_id" => attempt_id,
                "instance_name" => instance_name,
                "start_time" => start_time,
            })
            .first(&mut conn)
            .await
            .unwrap()
            .unwrap();

    add_attempt_resources(&mut conn, batch_id, job_id, attempt_id, resources).await;

    Ok(delta_cores_mcpu)
}

async fn mark_job_complete(
    db: &Db,
    batch_id: u64,
    job_id: u64,
    attempt_id: &str,
    instance_name: &str,
    new_state: &str,
    status: &(u64, u64),
    start_time: u64,
    end_time: u64,
    reason: &str,
    resources: &Vec<Resource>,
) -> Result<i32, Box<dyn std::error::Error>> {
    let mut conn = db.get_conn().await?;
    let record: mysql_async::Row =
        r"CALL mark_job_complete(:batch_id, :job_id, :attempt_id, :instance_name, :new_state, :status, :start, :end, :reason, :time);"
        .with(params! {
            "batch_id" => batch_id,
            "job_id" => job_id,
            "attempt_id" => attempt_id,
            "instance_name" => instance_name,
            "new_state" => new_state,
            "status" => serde_json::to_string(status).unwrap(),
            "start" => start_time,
            "end" => end_time,
            "reason" => reason,
            "time" => SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis(),
        })
        .first(&mut conn)
        .await.unwrap().unwrap();

    add_attempt_resources(&mut conn, batch_id, job_id, attempt_id, resources).await;

    let delta_cores_mcpu: i32 = record.get(2).unwrap();

    Ok(delta_cores_mcpu)
}

async fn add_attempt_resources(
    conn: &mut mysql_async::Conn,
    batch_id: u64,
    job_id: u64,
    attempt_id: &str,
    resources: &Vec<Resource>,
) {
    let mut merged_resources = HashMap::new();
    for r in resources {
        *merged_resources.entry(&r.name).or_insert(0) += r.quantity;
    }

    r#"
INSERT INTO `attempt_resources` (batch_id, job_id, attempt_id, resource_id, quantity)
SELECT :batch_id, :job_id, :attempt_id, resource_id, :quantity
FROM resources
WHERE resource = :resource
ON DUPLICATE KEY UPDATE quantity = quantity;"#
        .with(merged_resources.iter().map(|(resource, quantity)| {
            params! {
                "batch_id" => batch_id,
                "job_id" => job_id,
                "attempt_id" => attempt_id,
                "resource" => resource,
                "quantity" => quantity,
            }
        }))
        .batch(conn)
        .await
        .unwrap();
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    let client = build_client();
    let instances_map = web::Data::new(Mutex::new(HashMap::<String, i32>::new()));
    let db = mysql_async::Pool::new(build_sql_config());
    let db_clone = db.clone();

    let instances_map_clone = instances_map.clone();
    tokio::spawn(async move {
        let instances_map = instances_map.to_owned();
        loop {
            send_instance_updates(&client, &instances_map).await;
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    });

    HttpServer::new(move || {
        App::new()
            .wrap(Logger::default())
            .app_data(web::Data::new(db_clone.to_owned()))
            .app_data(instances_map_clone.to_owned())
            .service(greet)
            .service(job_complete)
    })
    .bind_openssl(("0.0.0.0", 5000), build_ssl_acceptor_builder())?
    .workers(1)
    .run()
    .await
    .unwrap();

    db.disconnect().await.unwrap();
    Ok(())
}
